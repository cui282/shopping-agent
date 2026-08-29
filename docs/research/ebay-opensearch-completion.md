# eBay + OpenSearch 完整实现调研

核对日期：2026-08-27

本文记录把 Shopping Agent 从“有限 Browse 快照 + BM25 降级”推进到“可刷新商品索引、语义检索和中国大陆到手价证据”的可执行方案。本文不保存 API key、OAuth token、商品快照或运行时索引。

## 结论

1. 没有 Feed API 权限时，不能用 Browse API 声称拥有 eBay 全站离线商品库。可行的临时方案是运营维护关键词/类目清单，定时抓取有界 Browse 快照，并在 OpenSearch 无新鲜命中时使用不写索引的实时 Browse 兜底。
2. eBay Browse 搜索支持 `deliveryCountry`、`deliveryPostalCode` 和 `X-EBAY-C-ENDUSERCTX`，可以返回商品级配送摘要；但税费、进口费用和 HS Code/原产地并不对每个 listing 保证提供。
3. OpenSearch 2.17 支持 Faiss HNSW 的直接 `knn` 查询；efficient k-NN filter 必须放在 `knn` 子句内部。商品和查询必须由同一模型生成向量，或由明确的共享投影对齐。
4. 人民币换算可以接入官方参考汇率，但该结果只能标记为参考价；中国海关申报月汇率、HS Code、原产地、进口模式和税率必须来自独立海关/数据提供商证据。当前一般贸易路径已接入中国人民银行公开历史序列，按海关规则选择申报月的基准汇率。

## eBay 商品覆盖

eBay Browse API 支持按关键词、类目、GTIN 等搜索，并支持 `deliveryCountry` 和 `deliveryPostalCode` 过滤：

- [Browse API 概览](https://developer.ebay.com/api-docs/buy/api-browse.html)
- [Browse API 字段过滤器](https://developer.ebay.com/api-docs/buy/static/ref-buy-browse-filters.html)
- [Browse API 调用限制](https://developer.ebay.com/develop/get-started/api-call-limits)

Feed API 才提供按市场/品类下载、离线整理、存储和同步的文件路径：

- [Feed API](https://developer.ebay.com/api-docs/buy/api-feed.html)
- [Feed API Beta 集成流程](https://developer.ebay.com/api-docs/buy/api-feed_beta.html)

在没有 Feed 权限时，推荐实现：

```text
query_catalog.yaml
  -> 每 4 小时按关键词/类目分页抓取 Browse
  -> 去重、检查 itemEndDate、记录 snapshot_at/expires_at
  -> 离线生成向量并发布版本化 OpenSearch index
  -> 成功刷新后原子切换 alias
```

Browse API 的默认调用额度有限，查询目录和分页必须有上限。用户查询未命中快照时可以调用 Browse 实时兜底，但只返回 `live_unindexed` 语义的 Product Evidence，不在用户请求中创建或切换索引。

## 配送、税费与目的地

官方文档说明 `X-EBAY-C-ENDUSERCTX` 的 `contextualLocation` 可提高配送时效估算准确度，并且是 calculated shipping 的必要上下文；Browse 搜索响应的 `shippingOptions.shippingCost` 是商品级预计运费：

- [Browse API 请求头与配送上下文](https://developer.ebay.com/api-docs/buy/api-browse.html)
- [ShippingOptionSummary](https://developer.ebay.com/api-docs/buy/browse/types/gct%3AShippingOptionSummary)
- [Browse Item 详情](https://developer.ebay.com/api-docs/buy/browse/types/gct%3AItem)

实现时应：

- 要求或收集中国大陆收货邮编；仅有国家不能保证 calculated shipping 精度。
- 搜索时发送 `filter=deliveryCountry:CN`，并发送中国国家/邮编上下文。
- 将 `shippingCost`、`shippingCostType`、服务名和最早/最晚配送日期映射为 `ShippingQuoteEvidence`；报价有效期使用快照有效期，不应长期保留。
- 将 eBay 商品所在位置作为 `ship_from_country` 候选，但不能把它当成 `country_of_origin`。
- Browse `taxes` 只表示 eBay 可能代收的销售税/VAT 等，不等于中国进口关税。

本项目在 2026-08-27 的实测结果：

- Production OAuth 和 Browse 搜索均返回 200。
- 带 `deliveryCountry:CN` 和中国目的地上下文的 10 条 Browse 结果全部包含运输摘要，其中 8 条包含配送日期。
- 对 10 条 listing 调用 `getItem` 时，没有返回 `shippingOptions` 或 `taxes` 明细，因此不能把 `getItem` 当成每个商品都可用的报价源。

## 语义向量

OpenSearch 支持使用预训练 sentence-transformer 模型生成 ingest 和 query embedding，也支持外部服务先生成向量，再使用 `knn_vector` 查询：

- [OpenSearch text embedding processor](https://docs.opensearch.org/latest/ingest-pipelines/processors/text-embedding/)
- [OpenSearch 预训练模型](https://docs.opensearch.org/latest/ml-commons-plugin/pretrained-models/)
- [OpenSearch hybrid search](https://docs.opensearch.org/latest/query-dsl/compound/hybrid/)
- [OpenSearch efficient k-NN filtering](https://docs.opensearch.org/latest/vector-search/filter-search-knn/efficient-knn-filtering/)
- [Sentence Transformers semantic search](https://www.sbert.net/examples/sentence_transformer/applications/semantic-search/README.html)

当前 `api.pingcodes.cc/v1` 的 `/models` 没有 embedding 模型，标准 `text-embedding-3-small` 请求返回 `model_not_found`。因此不能把 `gpt-5.6-luna` 当商品 embedding 模型。

推荐实现一个可选的 Python embedding 服务：

1. 使用同一个固定版本的多语言 SentenceTransformer 生成商品和查询向量。
2. 建库脚本记录模型名称、版本、维度和归一化方式。
3. 商品索引 mapping 的 `knn_vector.dimension` 从 manifest 生成，不再手写假定值。
4. 语义服务不可用时降级为 BM25 + 标量过滤，并在 readiness 和结果 provenance 中明确显示。
5. 不能把现有 hashing 1024 维向量与新的语义向量混用；切换模型必须重建索引。

对于当前规模，使用外部/本地 Python embedding 服务比把模型部署在 OpenSearch JVM 内更容易控制资源；OpenSearch ML Commons 本地模型是可选替代方案。

## 汇率与中国进口税

ECB Data Portal 提供 SDMX 形式的日度参考汇率，适合作为带来源、观测时间和有效期的 `mid_market_reference`：

- [ECB SDMX Web Services](https://data.ecb.europa.eu/help/getting-data-web-services-sdmx-0)
- [ECB Exchange Rates API 数据查询](https://data.ecb.europa.eu/help/api/data)
- [ECB 欧元参考汇率](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)

该参考汇率不能冒充支付成交价，也不能替代海关申报月汇率。项目应保留两份独立证据：

- `price_conversion`：商品比价用的实时/参考换算。
- `customs.valuation.customs_conversion`：海关申报月、独立于比价汇率的计税换算。

中国海关公开税率查询页面可以核对税号、最惠国/普通/暂定税率、原产地措施、增值税和消费税；项目调用其只读查询接口，并对按商品名无结果的情况按 HS Code 重试：

- [海关政务服务税率查询](https://online.customs.gov.cn/ociswebserver/pages/jckspsl/index.html)
- [税率查询详情](https://online.customs.gov.cn/ociswebserver/pages/jckspsl/detail.html)
- [中华人民共和国进出口税则](https://www.gov.cn/zhengce/zhengceku/2023-01/02/5734605/files/eafdb87c9c654702a5cd7f5f11dca10f.pdf)
- [海关税收征管规则](https://english.customs.gov.cn/statics/53a77dc4-c835-460a-b773-f5f7cef63a33.html)

中国人民银行公开汇率序列提供各主要币种的历史观察值，海关规则规定月度计税汇率采用前一月
第三个星期三（法定节假日使用第四个星期三）的基准汇率：

- [PBOC 汇率历史序列](https://wzdt.pbc.gov.cn/huilv/llChart1.jsp)
- [海关征税规则中的月度汇率口径](https://english.customs.gov.cn/statics/53a77dc4-c835-460a-b773-f5f7cef63a33.html)

因此完整到手价必须接入一个能提供以下字段的海关/贸易数据 provider：

```text
hs_code
country_of_origin
ship_from_country
destination_country=CN
import_regime
rate_type
tariff_rate/import_vat_rate/consumption_tax_rate 或明确的 seller_collected_tax_cny
customs.valuation（CIF 组成和申报月汇率）
provider/source_reference/effective_date
```

缺少上述证据时，商品可以继续展示，但不能进入到手价排序；代码不能从平台、商品标题或发货地臆测 HS Code、原产地或税率。

## 推荐的代码落点

1. `scripts/fetch_ebay_browse_snapshot.py`：支持查询目录、多市场、CN 配送过滤、邮编上下文、分页、增量快照和过期标记。
2. `app/data/ebay_browse.py`：把 Browse shipping summary、库存/结束时间和明确的 HS/origin aspects 投影为共享 Product Evidence；不在客户请求中建索引。
3. `scripts/build_product_opensearch.py`：统一 Amazon/eBay 文档、生成同空间 embedding、创建版本化 index、刷新后原子切换 alias。
4. `app/tools/product_search.py`：一次 OpenSearch 直接 ANN/k-NN 请求；标量过滤在 k-NN 内部使用 efficient filter，Embedding 不可用时再走全文降级。
5. `app/tools/ebay_browse.py`、`app/tools/fx_reference.py` 和 `app/tools/customs_provider.py`：在价格计算前补全可获得的 FX、shipping 和 customs 证据，缺失时返回结构化 exclusion，不伪造金额。
6. `app/config.py` 和 `.env.example`：增加 embedding、目的地邮编、FX provider、customs provider、快照刷新周期和查询目录配置。
7. `app/schemas.py`、`docs/API_CONTRACT.md` 和前端：增加快照新鲜度、语义降级、运费报价类型和到手价排除说明。

## 验收标准

- 同一个 embedding 模型生成商品向量和查询向量；维度/模型不一致时拒绝启动或查询。
- OpenSearch 请求包含 Faiss HNSW k-NN 和 k-NN 内部过滤；限制性价格/平台/配送条件不会变成 post-filter。
- 快照任务可重复运行，旧 alias 可回滚；快照时间保留为溯源元数据，当前阶段不作为 ANN 召回的失效门槛。
- `deliveryCountry=CN`、中国邮编、shipping cost 和时效可追溯到 eBay 请求/快照。
- FX、customs 缺失时报告明确排除原因，不把商品价格当作到手价。
- 覆盖快照命中、快照未命中实时 Browse 兜底、API 429/401/403、过期、空运费、无 HS Code、向量服务不可用和 alias 回滚。
- 运行 `make verify`，并在 1280px、375px、320px 下检查 live、degraded、empty 和 error 状态。
