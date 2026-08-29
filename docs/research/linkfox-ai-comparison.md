# LinkFox AI 能力调研与 Shopping Agent 对照

核对日期：2026-08-28
调研范围：LinkFox AI、LinkfoxAI、LinkFox Agent，以及其公开资料中相邻的 LinkFox Claw。外部证据只使用 LinkFox 官网、官方产品页和官方 Wiki；没有采用二手文章、媒体报道或第三方测评。登录后产品页面和需要账号授权的业务调用没有被本次调研当作已实测结果。

## 结论摘要

1. **产品定位不同。** LinkfoxAI 官方把自己定义为面向跨境电商团队的 AI Agent OS，重点是选品、市场/竞品分析、评论洞察、Listing、素材制作、商品资料和运营流程；它不是公开资料意义上的消费者跨平台购物搜索与结算服务。[官方产品介绍](https://wiki.linkfox.com/introduction/what-is-linkfox) [LinkFox Agent 官网](https://www.linkfox.com/agent)
2. **LinkFox 在卖家研究和运营自动化上更宽。** 官方文档描述了多数据源选品漏斗、利润测算、专利/合规检查、Listing 生成与 Amazon 刊登，以及 Skill、定时任务、团队资产和店铺经营看板。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test) [技能市场](https://wiki.linkfox.com/features/skills) [定时任务](https://wiki.linkfox.com/workspace/scheduled-tasks)
3. **Shopping Agent 在证据约束和消费者到手价上更明确。** 本仓库定义了 Marketplace Gateway、Product Evidence、Product Variant、Exact Offer Comparison、Supported Destination、Landed Cost、Research Snapshot 和 Remembered Preference 等边界；LinkFox 公开资料没有证明同款变体身份校验、跨平台精确报价、面向中国大陆的逐 offer 运费/税费计算或不可变研究快照。
4. **“30+ 工具”和广泛平台覆盖应视为官方声明。** LinkFox 公开页面列出若干工具和平台，但没有公开完整的工具清单、每个工具的接口 schema、覆盖站点矩阵或数据新鲜度 SLA。较新的 FAQ 还说明当前文档以亚马逊运营场景为准。[Agent 官网](https://www.linkfox.com/agent) [FAQ](https://wiki.linkfox.com/resources/faq) [文档索引](https://wiki.linkfox.com/llms.txt)

## 能力矩阵

| 能力面 | LinkFox AI 公开资料可核验内容 | 与本仓库 Shopping Agent 的对照 | 证据边界 |
| --- | --- | --- | --- |
| 商品搜索/选品 | 支持按关键词、类目、竞品 ASIN/链接/图片做候选筛选；字段包括价格、月销量、评分、评论数、重量、尺寸、配送方式等。 | Shopping Agent 面向 shopper 的 Product Research；还支持 Exact Offer Comparison，但要求 Product Variant 的 Identity Evidence。 | LinkFox 的“选品”是卖家候选池和竞品研究，不等同于已证明同款的跨平台 offer 比价。[选品分析](https://wiki.linkfox.com/features/product-selection) |
| 平台覆盖 | 官网/文档点名 Amazon、Walmart、Temu、TikTok/TikTok Shop、Shopee、Lazada；智选测款 P1 明确写“当前支持 Amazon”，FAQ 也称当前文档按亚马逊场景编写。 | Shopping Agent 的规范化网关覆盖 Amazon、Shopee、AliExpress、eBay，并把每个平台可用性和来源暴露在结果中。 | 只能确认这些名称被官方资料提及；不能据此确认每个平台、每个功能都可用。[Agent 官网](https://www.linkfox.com/agent) [词库](https://wiki.linkfox.com/workspace/lexicon) [智选测款](https://wiki.linkfox.com/solutions/smart-product-test) [FAQ](https://wiki.linkfox.com/resources/faq) |
| 数据来源/API | 官方资料提到 SellerSprite、Keepa、EchoTik、SIF、Google Trends、ABA、极目、1688、Amazon 前台、智慧芽、睿观等；称基于 MCP 标准化接入 30+ 工具。 | Shopping Agent 采用配置的 Data Provider Channel/Marketplace Gateway；生产缺少渠道时 fail closed，并保留 provider、source、retrieved_at 和失败原因。 | LinkFox 公开 OpenAPI 只描述无需认证的文档发现 API；业务能力需要授权账号，数据 Skill 通常需要 `LINKFOXAGENT_API_KEY`。没有公开完整业务 API schema。[技能市场](https://wiki.linkfox.com/features/skills) [Agent 使用说明](https://wiki.linkfox.com/agent-instructions.txt) [OpenAPI](https://wiki.linkfox.com/openapi.json) |
| 价格/比价 | 选品与市场分析可比较价格、折扣、价格带、评分、BSR；Keepa Skill 用于历史价格/BSR，官方案例描述把价格曲线与 BSR 放在一起分析。 | Shopping Agent 的价格转换保留原币、换算来源和观测时间；Exact Offer Comparison 只排名有身份依据的同一变体。 | LinkFox 公开资料没有给出跨平台同款证明或支付/结算价格保证。[竞品与市场分析](https://wiki.linkfox.com/scenarios/competitor-market-analysis) [Agent 案例](https://wiki.linkfox.com/resources/cases/agent) |
| 物流/税费 | P2/P5 可筛配送方式、重量尺寸、FBA 费用、头程费用、平台佣金、VAT、退货损耗并估算毛利；店铺授权场景覆盖订单/物流，官网还宣传物流调度和追踪。 | Shopping Agent 的 Landed Cost 需要中国大陆目的地、逐 offer shipping quote、FX、customs/HS/origin evidence；缺证据的候选不进入到手价排名。 | LinkFox 资料里的 VAT/物流是卖家利润或履约模型；未核验到逐 offer 目的地运费、保险、汇率 provenance、进口关税/消费税或中国海关估值 API。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test) [店铺授权](https://wiki.linkfox.com/resources/shop-authorization) [Agent 官网](https://www.linkfox.com/agent) |
| 智能规划/自动化 | 智选测款为 P1-P9 工作台；P1-P6 可自动执行，P7 为人工决策节点，随后生成 Listing 并可经 SP-API 刊登。Skill 可安装、创建、上传、融合；定时任务支持每日/每周/月度、手动执行和提醒。 | Shopping Agent 有确定性 planner、clarification、rerun、constraint relaxation，但没有 LinkFox 式卖家刊登、Skill 市场、店铺/广告/订单流程。 | 自动模式、人工节点和真实刊登均有官方文档描述；是否对所有平台开放仍不确定。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test) [批量运营自动化](https://wiki.linkfox.com/scenarios/bulk-automation) |
| 报告 | 市场分析生成综合结论、分项分析、调研草稿、预览/下载；Agent 官网宣传专家级 Excel 报告；智选测款 P7 有权重评分、漏斗、趋势、价格带和推荐/观望/放弃建议。 | Shopping Agent 从同一 Research Snapshot 确定性渲染 Markdown、JSON、PDF，并带 provider coverage、FX、shipping/tax exclusions 和 snapshot effective time。 | LinkFox 的报告/Excel 能力有公开说明，但公开资料没有稳定报告 schema 或完整 provenance contract。[市场分析](https://wiki.linkfox.com/features/analysis) [查看结果](https://wiki.linkfox.com/introduction/view-results) |
| 用户记忆/资料复用 | Claw 官网宣传“私有记忆”和保留上下文；Claw 帮助说明新任务会清空当前对话历史但不影响已存储记忆。Agent 侧另有商品库、词库、团队资产、历史会话。 | Shopping Agent 有显式 Remembered Preference、Task Override、remember/forget 命令和 preference provenance；记忆不会由普通研究结果隐式更新。 | LinkFox 公开资料没有记忆字段、召回规则、偏好优先级、导出/删除接口或 retention contract；商品库/词库不能直接等同于 shopper preference memory。[Claw 产品页](https://os.linkfox.com/claw?from=home) [Claw 省积分贴士](https://wiki.linkfox.com/resources/claw-points-tips) [商品库](https://wiki.linkfox.com/features/product-library) |
| 可观测/运营 | 会话显示消息、工具调用、执行过程、任务产出和右侧预览；定时任务记录运行中/成功/失败/超时、摘要/异常原因和执行上下文；店铺看板展示数据来源与截止时间。 | Shopping Agent 通过 readiness、typed WebSocket events、tool_end、provider/recall provenance、durable snapshot 和可选 Langfuse 暴露运行状态。 | LinkFox 的用户可见过程和运行记录已公开说明；未找到公开 typed event contract、服务健康/就绪 API、SLO 或统一 provider reason code。[查看结果](https://wiki.linkfox.com/introduction/view-results) [定时任务](https://wiki.linkfox.com/workspace/scheduled-tasks) [店铺经营助手](https://wiki.linkfox.com/features/store-operations) |

## 分项证据

### 1. 商品搜索、选品和比价

- LinkfoxAI 的选品分析支持关键词、类目、竞品线索，入口包括“选品工具”“对标选品”和“选品方法”；官方文档把候选产品池用于后续市场分析、Listing、图片和视频流程。[选品分析](https://wiki.linkfox.com/features/product-selection)
- 对标选品可以输入商品 ID、商品链接或商品主图，并按平台和销售国家继续分析同类商品、替代商品、价格带和差异化空间。[选品分析](https://wiki.linkfox.com/features/product-selection)
- 市场分析要求输入 3-20 个竞品 ASIN/链接，建议统一比较价格、优惠、评分、评论数、BSR、卖点、图片结构、关键词和差评痛点；官方把它定位为运营决策，而非消费者购物结算。[竞品与市场分析](https://wiki.linkfox.com/scenarios/competitor-market-analysis)
- 商品库的“参考 Listing”可保存竞品或参考对象，并查看标题、图片、关键词、价格、评分、BSR 和五点描述；这支持参考对比，但不构成跨平台 Product Variant 身份证明。[商品库](https://wiki.linkfox.com/features/product-library)
- 官方文档没有公开“同一变体”的跨平台匹配规则、GTIN/型号等 identity contract，也没有证据表明结果会像 Shopping Agent 的 Exact Offer Comparison 一样只对已证明同款的 offer 排名。[商品库](https://wiki.linkfox.com/features/product-library) [选品分析](https://wiki.linkfox.com/features/product-selection)

### 2. 平台覆盖和数据来源

- LinkFox Agent 官网列出 Amazon、Shopee、TikTok Shop、Lazada，并称一句话可调用卖家精灵、Keepa、TikTok 等 30+ 工具；同一页面还把 Amazon、Walmart、Temu、TikTok、Google Trends 列为市场调研的多源数据。[Agent 官网](https://www.linkfox.com/agent)
- LinkfoxAI 词库文档把 Amazon、Walmart、TikTok Shop、Temu、Shopee、Lazada 列为可管理的多渠道；技能市场的工具筛选还列出 Keepa、亚马逊前台、Sif、卖家精灵、谷歌趋势、EchoTik、智慧芽、睿观、LinkFoxAI 和 1688。[词库](https://wiki.linkfox.com/workspace/lexicon) [技能市场](https://wiki.linkfox.com/features/skills)
- 智选测款 P1 的公开参数把目标平台写为“当前支持 Amazon”，并说明原始 ASIN 池来自 Amazon 前台及 SIF 关键词反查、卖家精灵-选产品；这比官网的广泛平台宣传更具体。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- P4 明确列出 Google Trends、ABA 数据挖掘、SIF 关键词反查、极目月度数据和 Keepa；P5 连接站点 FBA 费用、1688 以图搜图和采购价；P6 使用智慧芽、睿观和卖大律做专利、合规、商标、版权或 TRO 风险检查。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- 官方 Skill 案例还提到 Amazon 商品详情、Keepa、Jungle Scout、Alexa Shopping QA、Keepa Product Request，以及 EchoTik 商品视频的播放、互动和销量 GMV 字段；这些是公开案例里的具体调用示例，不应扩大为所有账号都默认开通。[Agent 案例](https://wiki.linkfox.com/resources/cases/agent) [EchoTik 案例](https://wiki.linkfox.com/resources/cases/skill/echotik-product-video)
- LinkFox 的开发者文档明确说：当前公开的是无需认证的文档发现接口，业务侧能力需要在产品中使用授权账户；`openapi.json` 的实际路径只有文档 API 的 `GET /api/v1/status`。需要调用数据能力的 Skill 通常配置 `LINKFOXAGENT_API_KEY`，但官方没有在公开 OpenAPI 中给出这些业务 Skill 的完整 endpoint/schema。[文档索引](https://wiki.linkfox.com/llms.txt) [Agent 使用说明](https://wiki.linkfox.com/agent-instructions.txt) [OpenAPI](https://wiki.linkfox.com/openapi.json) [技能市场](https://wiki.linkfox.com/features/skills)

### 3. 价格、物流、利润和税费

- 选品分析的候选字段包含价格、BSR、月销量、月销售额、重量、尺寸和配送；产品条件筛选支持价格、销量、评分、评论数、上架时间等条件。[选品分析](https://wiki.linkfox.com/features/product-selection)
- 竞品与市场分析覆盖竞品价格和价格异常；官方案例用 Keepa 拉取两个 ASIN 的六个月历史价格与 BSR 曲线，并分析降价换排名或稳定价格等策略。[竞品与市场分析](https://wiki.linkfox.com/scenarios/competitor-market-analysis) [Agent 案例](https://wiki.linkfox.com/resources/cases/agent)
- 智选测款 P5 的卖家利润模型可配置轻小件/大件毛利率、售价、1688 供应商搜索词、头程费用、FBA 费用、平台佣金、VAT、广告预估、退货损耗和供应商数量下限；它输出毛利达标的 ASIN 和供应商表格。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- 店铺授权文档把“订单与物流”列为授权场景，包含订单详情、处理发货、跟踪物流；官网 Agent 页面另宣称可按海运、空运、专线、海外仓匹配物流，并生成面单和回传追踪号。[店铺授权](https://wiki.linkfox.com/resources/shop-authorization) [Agent 官网](https://www.linkfox.com/agent)
- **未核验到消费者到手价能力：** 所查官方资料没有公开逐 offer 目的地运费 quote、最早/最晚到货窗口、保险、付款 FX、HS Code、country of origin、进口关税/消费税/进口 VAT、海关估值或面向中国大陆的税费规则。因此不能把 LinkFox 的售价、FBA/头程/平台费用或 VAT 参数称为 Shopping Agent 意义上的 Landed Cost。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test) [店铺授权](https://wiki.linkfox.com/resources/shop-authorization)

### 4. 智能规划、Skill 和自动化

- 智选测款是从原始 ASIN 池到选品报告、Listing 生成和刊登执行的 9 步工作台；P1-P6 在自动模式可无人值守，P7 需要人工确认报告，再进入 P8-P9。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- P1 能按关键词、品类或 ASIN 获取候选池；P2 做销量、评分、评论数、卖家数量、配送方式、卖家所在地、上架时长和销量增长等硬性过滤；P3 评估 Top10 收入占比、新品占比、退货率和 FBA 占比；P4 验证供需、YOY 和季节性；P5 做利润测算；P6 做 IP/合规风险检查；P7 用权重和阈值给出推进、观望或放弃建议。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- Skill 市场支持安装官方或社区 Skill、个人/团队管理、创建自定义 Skill、上传 Skill 和融合多个 Skill；使用第三方 AI 助手时，官方文档要求配置 `LINKFOXAGENT_API_KEY`。[技能市场](https://wiki.linkfox.com/features/skills)
- 定时任务可按每天、每周、每月或不重复执行，支持启停、编辑、手动执行、分组、删除，以及飞书、钉钉或邮件结果提醒；运行记录可回看执行摘要、异常原因和 AI 执行上下文。[定时任务](https://wiki.linkfox.com/workspace/scheduled-tasks)
- LinkFox Claw 是相邻的 7×24 小时云端助手产品，官方页面宣传云端设备、私有记忆、专属技能库和消息渠道；这比普通 LinkfoxAI 任务更接近常驻自动化，但不应和 LinkFox Agent 的公开卖家研究能力混为一谈。[Claw 产品页](https://os.linkfox.com/claw?from=home) [Claw 产品说明](https://agent.linkfox.com/claw)

### 5. 报告、产出和复用

- 市场分析支持综合结论、分项分析、推荐追问、调研草稿、预览和下载；可以把市场、竞品、评论、合规和关键词结果合并成一份调研报告。[市场分析](https://wiki.linkfox.com/features/analysis)
- 结果页按任务类型显示报告、素材、评分、预览或下载；市场分析和 Listing 结果可预览、下载，报告可加入调研草稿，结果也可保存到商品库或通过只读分享链接复用。[查看结果](https://wiki.linkfox.com/introduction/view-results) [新任务](https://wiki.linkfox.com/workspace/new-task)
- 智选测款 P7 报告包含综合评分、市场/需求/专利/趋势/供应链分、原始候选池和通过率漏斗、需求趋势、合规分布、价格带、ASIN 散点图、流量结构和推荐卡片；P8 还能产出 Listing 字段、模板文件和 SKU。[智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- Agent 官网把自动生成专家级 Excel 报告列为能力，描述包含数据图表、分析结论和执行建议；这属于官网产品声明，公开资料没有提供可供外部系统依赖的固定 Excel schema。[Agent 官网](https://www.linkfox.com/agent)

### 6. 用户记忆、资产和上下文

- Claw 官方产品页宣传“私有记忆”和“保留全部上下文与记忆”；Claw 帮助页说明开启新任务会清空当前对话历史，但不影响已存储的记忆。[Claw 产品页](https://os.linkfox.com/claw?from=home) [Claw 省积分贴士](https://wiki.linkfox.com/resources/claw-points-tips)
- 普通 LinkfoxAI 公开文档把可复用资料拆为商品库、词库、Skill、团队资产和历史会话：商品库保存图片、规格、SKU、链接、Listing 和历史任务结果；词库保存关键词、品牌词、敏感词和禁用表达；任务可以引用它们继续工作。[商品库](https://wiki.linkfox.com/features/product-library) [词库](https://wiki.linkfox.com/workspace/lexicon) [创建任务](https://wiki.linkfox.com/introduction/create-task)
- **未核验到记忆 contract：** 公开资料没有给出 Claw 私有记忆的字段类型、显式 remember/forget 操作、冲突优先级、任务隔离规则、导出/删除接口、保留期限或是否影响商品排名。Shopping Agent 的 Remembered Preference 和 Task Override 不能假设由 LinkFox 记忆直接替代。[Claw 产品页](https://os.linkfox.com/claw?from=home) [Claw 省积分贴士](https://wiki.linkfox.com/resources/claw-points-tips)

### 7. 可观测性和运营闭环

- 普通任务会显示消息、工具调用、执行过程、任务产出和右侧预览；历史任务可回看会话、修改标题、复制分享链接或删除。[新任务](https://wiki.linkfox.com/workspace/new-task) [查看结果](https://wiki.linkfox.com/introduction/view-results)
- 定时任务详情显示状态、运行时间、下次运行时间、执行次数、执行 Agent、关联商品/Skill、任务说明和运行记录；运行记录区分运行中、成功、失败、超时，并显示摘要或异常原因，还能查看一次运行的执行过程。[定时任务](https://wiki.linkfox.com/workspace/scheduled-tasks)
- 店铺经营助手的看板可按店铺、站点、时间范围和管理/专员视角查看；页面显示“数据来源和截止时间”，并下钻到销售、订单、广告、库存、售后和单品数据。[店铺经营助手](https://wiki.linkfox.com/features/store-operations)
- LinkFox 官方帮助明确提醒：Agent 可能编造数据、把旧数据当最新、误解 ASIN/关键词/站点、在工具无结果时继续判断；官方建议要求列出依据、区分事实/推断/建议、保留来源并人工复核。[AI 幻觉应对](https://wiki.linkfox.com/resources/ai-hallucination)
- **未核验到平台级可观测 contract：** 公开资料没有找到类似 Shopping Agent 的 readiness 组件状态、typed WebSocket event schema、统一 provider source/status/failure reason、Research Snapshot lineage、公开健康检查或 Langfuse/SLO 约定；只能确认上述用户界面级运行记录和看板能力。[文档索引](https://wiki.linkfox.com/llms.txt) [OpenAPI](https://wiki.linkfox.com/openapi.json)

## 对本仓库的工程启示

1. 可借鉴 LinkFox 的 Skill/工具编排、阶段化选品漏斗、报告模板、定时监控、团队资料和消息提醒，用于扩展 Shopping Agent 的运营效率。
2. 不应以 LinkFox 的卖家利润模型替代本仓库的 Landed Cost：商品价格、FBA/头程、VAT 或平台费用不是中国大陆消费者的逐 offer 到手价；仍需保留 shipping quote、FX、customs classification/origin 和税率证据。
3. 不应把 ASIN/标题/图片相似度直接变成 Exact Offer Comparison。LinkFox 公开资料没有提供跨平台变体身份 contract，本仓库应继续要求 Product Variant 的 Identity Evidence。
4. 可借鉴 LinkFox 的执行过程和运行记录，但本仓库的 provider coverage、data mode、recall provenance、snapshot immutability、report lineage 和 fail-closed 语义仍应由 API 契约明确表达。
5. LinkFox 的 Claw 私有记忆是可参考的产品方向，不足以替代本仓库显式 Remembered Preference、Task Override、remember/forget 和透明 preference_decisions；任何持久化偏好仍应有可审计边界。

## 当前不能从一手公开资料确认的点

- “30+ 工具”完整名单、每个工具的版本/套餐/调用额度、数据字段和 API schema。
- Amazon、Walmart、Temu、TikTok Shop、Shopee、Lazada 等平台在各个 LinkFox 功能中的实际可用矩阵；当前公开 FAQ 和智选测款文档偏向 Amazon。
- LinkFox 是否提供消费者视角的跨平台同款 offer 比价、Product Variant identity proof、结算价或购买链接管理。
- 逐 offer 的收货目的地运费、配送窗口、保险、FX、HS Code/原产地、进口关税、进口 VAT/消费税和中国海关估值能力。
- 业务 API 的公开 endpoint、webhook、限流、错误码、稳定字段、数据新鲜度和 SLA；官方公开 OpenAPI 目前是文档发现 API。
- Claw/Agent 私有记忆的字段、召回与冲突规则、显式删除/导出、保留期限和任务隔离方式。
- 报告的稳定机器可读 schema、证据 provenance contract、跨任务 lineage 和可供外部系统订阅的运行事件。

## 一手来源清单

- [LinkFox Agent 官网](https://www.linkfox.com/agent)
- [LinkfoxAI 是什么](https://wiki.linkfox.com/introduction/what-is-linkfox)
- [LinkfoxAI 官方文档索引](https://wiki.linkfox.com/llms.txt)
- [LinkfoxAI Agent 使用说明](https://wiki.linkfox.com/agent-instructions.txt)
- [LinkfoxAI OpenAPI](https://wiki.linkfox.com/openapi.json)
- [选品分析](https://wiki.linkfox.com/features/product-selection)
- [市场分析](https://wiki.linkfox.com/features/analysis)
- [智选测款](https://wiki.linkfox.com/solutions/smart-product-test)
- [商品库](https://wiki.linkfox.com/features/product-library)
- [词库](https://wiki.linkfox.com/workspace/lexicon)
- [技能市场](https://wiki.linkfox.com/features/skills)
- [定时任务](https://wiki.linkfox.com/workspace/scheduled-tasks)
- [批量运营自动化](https://wiki.linkfox.com/scenarios/bulk-automation)
- [竞品与市场分析](https://wiki.linkfox.com/scenarios/competitor-market-analysis)
- [店铺授权](https://wiki.linkfox.com/resources/shop-authorization)
- [店铺经营助手](https://wiki.linkfox.com/features/store-operations)
- [查看结果](https://wiki.linkfox.com/introduction/view-results)
- [新任务](https://wiki.linkfox.com/workspace/new-task)
- [AI 幻觉应对](https://wiki.linkfox.com/resources/ai-hallucination)
- [LinkFox Claw 产品页](https://os.linkfox.com/claw?from=home)
- [LinkFox Claw 省积分贴士](https://wiki.linkfox.com/resources/claw-points-tips)
- [本仓库能力基线：CONTEXT.md](../../CONTEXT.md)
- [本仓库能力基线：API_CONTRACT.md](../API_CONTRACT.md)
