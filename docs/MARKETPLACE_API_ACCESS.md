# 国际购物平台 API 申请与接入指南

> 核对日期：2026-07-30。平台资格、配额和协议会变化，提交申请前应重新打开本文链接确认。本指南是工程接入建议，不构成法律意见。

## 先看结论

Shopping Agent 需要的是面向消费者的商品发现/联盟数据 API，而不是卖家用来管理自有店铺的 API。

| 平台 | 适合商品比价的官方产品 | 当前可申请性 | 不应混用的卖家 API |
| --- | --- | --- | --- |
| Amazon | Creators API | 有明确入口，但需 Associates 正式通过及近 30 天至少 10 笔合格销售；AI 分析型比价应先取得书面许可 | Selling Partner API (SP-API) |
| eBay | Buy Browse API | 开发者入口清晰；官方页面对生产审批要求存在冲突，应以实际 keyset 权限和 Support 书面答复为准 | Sell Inventory API |
| AliExpress | Affiliate `product.query`（若仍获准） | 公开文档仍可见，但所属 Affiliate API 栏目已标记“已废弃”，新申请状态不明确 | Seller/Open Platform 店铺接口 |
| Shopee | 各站点 Affiliate Open API | 按国家/地区单独开放并审核，没有可确认的全球统一申请条件 | Open Platform Product API（授权店铺） |

四个平台都不能直接套入本仓库当前的 `GET ?query=&top_k=` 协议。正确做法是在服务端部署一个自有网关，由网关处理各平台 OAuth、签名、地区参数、限流和字段映射，再把网关地址与网关密钥配置给 Shopping Agent。

## 申请前准备

先准备一套可以反复用于平台审核的材料：

1. 真实可访问的网站或应用、企业邮箱、主体和联系人信息。
2. 隐私政策、服务条款、联盟披露和联系方式。
3. Shopping Agent 的页面截图或可访问测试环境，清楚标出平台来源、价格时间、跳转链接和免责声明。
4. 一页数据流：用户查询 -> 后端调用平台 -> 短时缓存 -> 标准化 -> 展示 -> 用户主动跳转平台。
5. 目标国家/站点、预计日请求量、现有流量、转化/订单数据和盈利方式。
6. 数据保留周期、删除机制、密钥管理、日志脱敏和供应商故障处理说明。

申请描述应明确写“价格比较/商品发现”，不要用含糊的“采集数据”表述，也不要隐瞒会使用 LLM 做推荐。平台是否允许内容进入模型推理、日志或训练集，必须分别确认。

## Amazon

### 应申请什么

新项目应申请 **Creators API**。Amazon 已正式将 PA-API 5 标记为弃用，旧调用可能返回 `403 AccessDeniedException`；Creators API 是当前受支持的商品目录入口，提供 `SearchItems`、`GetItems`、`GetVariations` 等操作。[PA-API 5 弃用通知](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/paapiv5-deprecation)、[Creators API 介绍](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/introduction)

不要把 SP-API 当成公共比价接口。`searchCatalogItems` 虽然支持关键词搜索，但前置条件包括 Selling Partner 授权、Product Listing 角色审批和应用注册角色，定位是卖家/供应商工作流。[SP-API Catalog 搜索前置条件](https://developer-docs.amazon.com/sp-api/lang-zh/docs/search-catalog-items)

### 申请步骤

1. 在目标 Amazon 站点加入 Associates Program，并让账户获得最终接受。
2. 先通过普通联盟链接产生合格销售。官方当前资格说明要求近 30 天至少 10 笔 qualifying sales。
3. 由 Associates 主账户所有者登录 Associates Central，进入 `Tools > Creators API`。
4. 选择 `Create Application`，再添加 Credential；保存 `Credential ID`、`Credential Secret` 和 `Version`。
5. 为每个目标站点准备有效 `Partner Tag`，并确认该区域已批准 Creators API 权限。

申请入口与限制见[注册步骤](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/onboarding/register-for-creators-api)和[资格说明](https://affiliate-program.amazon.com/creatorsapi/docs/)。凭证通过 OAuth 2.0 client credentials 换取约一小时有效的 Bearer token；不同 credential version 对应不同区域 token endpoint。[官方 cURL/认证指南](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/get-started/using-curl)

### 对本项目最重要的合规点

- Amazon 的美国站政策明确讨论了把 Amazon 价格与其他网站价格放在 comparison format 中展示的情况，但要求同时显示 Amazon 最低新品价以及在提供时显示最低二手价。
- 同一政策又规定：未经事先书面同意，不得聚合、分析、提取或重新利用 Product Advertising Content；也不得直接用于训练或微调机器学习/基础模型。
- 商品图片不能自行缓存；图片链接及其他 Product Advertising Content 通常最多缓存 24 小时，之后必须刷新。低于每小时刷新频率时，还需在价格/库存旁显示时间戳及价格变化免责声明。
- 应用的主要目的必须是推广 Amazon 并向 Amazon 导流，返回的联盟链接参数不能修改。
- Amazon 的 Agent Terms 适用于代表用户采取自主或半自主动作的软件；请求必须用 `Agent/[agent name]` User-Agent 表明身份，不得规避 CAPTCHA，也不得隐瞒自动化访问。

这些要求见美国站 2026-04-14 更新的[Associates Program Policies](https://affiliate-program.amazon.com/help/operating/policies/#Associates%20Program%20IP%20License)。各站点适用各自协议，入口汇总在[Creators API License 页面](https://affiliate-program.amazon.com/creatorsapi/docs/en-us/license-agreement)。

因此，Shopping Agent 在接入 Amazon 前应把“跨平台排序、LLM 推理输入、日志内容、缓存、推荐文案和跳转方式”完整提交给 Associates Support，取得书面确认。一般联盟账号获批不等于这个具体 AI 比价数据流已经获批。未经明确书面许可，Amazon 数据不得进入任何模型训练、微调或嵌入索引构建流程。

### 网关需要保存的凭证

`Credential ID/Client ID`、`Credential Secret`、`Version`、目标站点 `Partner Tag` 和 marketplace。网关负责缓存 OAuth token、调用 `SearchItems`、保留 Amazon 返回的联盟链接、执行 24 小时内刷新，并输出仓库统一商品结构。

## eBay

### 应申请什么

使用 **Buy Browse API**。它面向购物发现，可按关键词、分类、GTIN 或图片搜索，并返回商品摘要、价格、图片和链接。[Browse API 官方介绍](https://developer.ebay.com/api-docs/buy/api-browse.html)

Sell Inventory API 用于卖家创建和管理自己的库存/刊登，不是全站比价数据源。[Sell Inventory API 概览](https://developer.ebay.com/api-docs/sell/inventory/static/overview.html)

### 申请步骤

1. 注册 [eBay Developers Program](https://developer.ebay.com/signin?tab=register)。官方建议使用企业邮箱，账户审核通常约一个工作日。[注册说明](https://developer.ebay.com/api-docs/static/gs_join-the-ebay-developers-program.html)
2. 在 [Application Keys](https://developer.ebay.com/my/keys) 创建 Sandbox 和 Production keyset。
3. 保存 `App ID/Client ID` 与 `Cert ID/Client Secret`。生产 keyset 启用前需完成 Marketplace Account Deletion/Closure Notifications 的订阅或退出选择。[创建 keyset](https://developer.ebay.com/api-docs/static/gs_create-the-ebay-api-keysets.html)、[OAuth 凭证](https://developer.ebay.com/api-docs/static/oauth-credentials.html)
4. 使用 client credentials grant 换取 Application access token；Browse API 不需要最终消费者登录授权。[OAuth token 类型](https://developer.ebay.com/api-docs/static/oauth-token-types.html)
5. 调用 Browse `item_summary/search`，并按目标站点发送 `X-EBAY-C-MARKETPLACE-ID`。若要联盟佣金，再加入 [eBay Partner Network](https://partnernetwork.ebay.com/) 并传递 Campaign ID。

### 生产审批的不确定性

官方资料目前互相矛盾：

- [Buy APIs Requirements](https://developer.ebay.com/api-docs/buy/buy-requirements.html) 仍写许多 Buy API 的生产使用面向合作伙伴，需要 EPN 业务模型审核、测试材料、Developer Support 审查和合同，且不保证获批。
- 当前 [EPN Developer Questionnaire](https://partnernetwork.ebay.com/page/developer-questionnaire) 又明确写 `EDP Browse API` 不需要额外审批，并把 `Price Comparison` 列为业务模型示例。

此外，当前 [eBay API License Agreement](https://developer.ebay.com/join/api-license-agreement) 的 Public Display 条款要求公开展示的 eBay 内容与非 eBay 内容视觉隔离，不得混合或合并。EPN 问卷允许选择 `Price Comparison` 并不自动豁免该展示条款。

稳妥顺序是先用 Production keyset 实测 Browse API。若 scope 不可用、返回 `403`，或准备商业上线，则向 Developer Support 提交业务模型、国家、截图、数据流和 Sandbox 测试步骤，并明确询问跨平台排序与同屏展示方式，取得书面确认。即使 Browse 本身无需额外审批，联盟佣金、提高配额和数据 Feed 仍有各自申请流程。

### 网关需要保存的凭证

`Client ID`、`Client Secret`、目标 `Marketplace ID`，以及可选的 EPN `Campaign ID`。网关负责生成/缓存 Application token，把仓库的 `query` 映射为 eBay 的 `q`，并统一 `itemId/title/price/currency/image/itemWebUrl` 等字段。

## AliExpress

### 当前状态

最接近本项目需求的历史官方接口是 `aliexpress.affiliate.product.query`，可按关键词、目的国家、目标币种和语言检索联盟推广商品，并返回价格、图片、商品链接等字段。[商品查询接口](https://developer.alibaba.com/docs/api.htm?apiId=45803)

但是，官方文档导航已将整个 **Affiliate API** 栏目标为“已废弃”，而单个 API 参考页仍在线。公开资料没有给出可确认的现行替代产品或新申请承诺。[Affiliate API 总览（已废弃）](https://developer.alibaba.com/docs/doc.htm?articleId=118192&docType=1&treeId=674)

### 可尝试的申请路径

以下是官方历史流程，不应视为 2026 年仍保证可申请：

1. 使用与 AliExpress Portals 相同的账号登录 `https://console.aliexpress.com`。
2. 激活开发者账号，选择 `Affiliate API` 类型并提交开发者审核。
3. 审核后创建应用，取得 `App Key` 与 `App Secret`。
4. 申请/确认 tracking ID，然后使用签名后的公共参数调用 `product.query`。

历史步骤见[Overall Flow For API Access](https://developer.alibaba.com/docs/doc.htm?articleId=118193&docType=1&treeId=674)和[How to Apply For App Key](https://developer.alibaba.com/docs/doc.htm?articleId=118195&docType=1&treeId=674)，两页均已被官方标记为废弃。

上线前必须通过 AliExpress/Open Platform 工单或客户经理确认：新应用是否仍可开通、当前替代 API、允许展示的字段、缓存期限、目标国家、联盟链接归因和跨平台比较用途。没有书面确认时，不应基于旧接口建设生产能力。

AliExpress Seller Open Platform 的旧说明要求卖家身份；自研开发者还要求企业主体，并只能授权本企业店铺，适合店铺运营而不是全站比价。该说明同样已标记废弃，因此只能用于理解接口边界，不能作为当前准入承诺。[卖家开发者历史说明](https://developer.alibaba.com/docs/doc.htm?articleId=108088&docType=1&treeId=503)

### 网关需要保存的凭证

在平台书面确认旧/新接口后，保存其颁发的 `App Key`、`App Secret`、tracking ID 及区域配置。网关负责参数签名、目标国家/语言/币种映射，并把联盟响应转换成仓库统一结构。

## Shopee

### 先区分两套产品

Shopee Open Platform 的 Product API 以 `shop_id` 和卖家授权为边界，例如 `get_item_list` 获取的是已授权店铺商品。它适合 ERP/卖家管理，不是 Shopee 全站商品搜索。[Shopee Open Platform 文档入口](https://open.shopee.com/documents?module=89&type=2)、[Shop Authorization 文档入口](https://open.shopee.com/documents?module=63&type=2)

面向内容推广和商品发现的候选是 **Affiliate Open API**，但它按站点运营。印尼站有独立的官方 [Open API Explorer V2](https://open-api.affiliate.shopee.co.id/explorer/v2)，页面使用 `AppId` 和 `Secret`；这不能证明同一凭证或资格适用于其他国家。

### 申请步骤

1. 选定目标 Shopee 国家/地区，在当地加入 Affiliate Program，并保持账号正常。
2. 从该站点 Affiliate Center 或官方 Help Center 查找 `Affiliate Open API` 申请表；不要拿 Seller Open Platform 的 partner key 替代。
3. 提交网站/社媒、流量、订单量、内容质量、目标使用方式和技术联系人。
4. 审批后获取该站点的 App ID/Secret，使用当地 Affiliate Open API endpoint，并确认商品查询字段、签名方式、配额、缓存和链接归因规则。

门槛并非全球统一。例如泰国官方当前要求 Open API 申请者每月超过 1,000 笔联盟订单，同时提供网站流量或社媒触达/互动证明，并保持高质量、非 clickbait 内容。[Shopee Thailand 官方申请条件](https://help.shopee.co.th/portal/4/article/198622-%E0%B8%82%E0%B8%B1%E0%B9%89%E0%B8%99%E0%B8%95%E0%B8%AD%E0%B8%99%E0%B8%81%E0%B8%B2%E0%B8%A3%E0%B8%82%E0%B8%AD%E0%B9%80%E0%B8%9B%E0%B8%B4%E0%B8%94-Affiliate-Open-API)

该数字只适用于上述泰国官方页面，不能外推至新加坡、印尼、菲律宾、马来西亚、越南、台湾或巴西；这些市场范围可参考 [Shopee 官方帮助中心](https://help.shopee.co.id/portal/4/article/73035-%5BTentang-Shopee%5D-Di-negara-negara-mana-saja-Shopee-tersedia%3F)。公开官方资料不足以确认所有站点是否接受新 API 申请；必须逐站向当地 Affiliate Support 核实。

### 网关需要保存的凭证

目标站点 Affiliate Open API 的 `App ID`、`Secret` 和区域 endpoint。网关负责 GraphQL/签名调用、地区隔离和字段统一。若手上只有 Open Platform `partner_id/partner_key + shop_id/access_token`，只能把该授权店铺作为数据源，不能把它描述为 Shopee 全站比价。

## 如何选择合法的第三方 API

“在 API 市场能买到”不等于拥有平台数据再授权权利。付款前要求供应商用合同或附件逐项回答：

1. 数据来自哪个平台、接口、联盟网络或授权数据 Feed？供应商能否出示授权链和转售/再许可权？
2. 许可是否覆盖公开展示、价格比较与排序、深链接、联盟归因、图片、缓存、历史存储、派生评分和 LLM 推理？
3. 许可覆盖哪些国家/站点、网站/移动端、商业或非商业场景？终止后多久必须删除数据？
4. 是否提供 `source`、marketplace、seller、商品/offer ID、币种、库存、抓取时间、价格类型、运费税费口径和最终商品 URL？
5. 数据刷新延迟、配额、429 策略、SLA、故障通知、字段变更通知、退款和退出条款是什么？
6. 是否有 DPA、子处理者清单、数据驻留、安全事件通知和密钥轮换机制？
7. 平台投诉、下架、纠错和审计由谁处理？供应商是否对数据权利作出保证并承担相应赔偿责任？

以下情况应直接拒绝：不披露上游来源；把“网页公开可见”当作转售授权；要求提供消费者 cookie、绕过 CAPTCHA 或使用未公开移动端接口；无法书面授予图片/价格/链接展示权；合同禁止比较、排序或向终端用户展示；宣称“官方”但无法提供平台合作证明。

第三方合同还必须与平台自身条件兼容。例如 Amazon 当前协议限制内容缓存和模型训练，也禁止转售、再分发或再许可 Program Content；第三方不能授予其自己没有的权利。[Amazon Associates Program Policies](https://affiliate-program.amazon.com/help/operating/policies/#Associates%20Program%20IP%20License)

## 与 Shopping Agent 的配置映射

本仓库不会把平台密钥发送到浏览器。推荐部署如下：

```text
React -> Shopping Agent FastAPI -> 自有 marketplace gateway -> 官方/合法第三方 API
```

| 平台 | 网关内部保存 | Shopping Agent 只配置 |
| --- | --- | --- |
| Amazon | Credential ID/Secret、Version、Partner Tag、marketplace | `AMAZON_API_ENDPOINT`、`AMAZON_API_KEY` |
| eBay | Client ID/Secret、Marketplace ID、可选 Campaign ID | `EBAY_API_ENDPOINT`、`EBAY_API_KEY` |
| AliExpress | 经确认的 App Key/Secret、tracking ID、区域 | `ALIEXPRESS_API_ENDPOINT`、`ALIEXPRESS_API_KEY` |
| Shopee | 对应站点 Affiliate App ID/Secret | `SHOPEE_API_ENDPOINT`、`SHOPEE_API_KEY` |
| 第三方 | 供应商 token、套餐和数据许可版本 | 对应平台 `*_API_ENDPOINT`、`*_API_KEY` |

网关向 Shopping Agent 暴露 `GET /{platform}/search?query=...&top_k=...`，使用自有 Bearer/API key 验证请求，并至少返回 `title`、`price`、`currency`。建议同时返回 `item_id`、`image_url`、`product_url`、`seller`、`availability`、`observed_at`、`marketplace`、运费/税费口径和上游来源，便于后续补强当前统一模型。

## Self-hosted Beta 验收清单

### 配置与启动

1. 复制 `examples/sandbox.env.example` 进行本地验收，或复制 `examples/live.env.example` 进行 live 配置；不要把两个模式混用。
2. Sandbox 只允许在非生产环境显式启用 `SANDBOX_MODE=true`。生产环境没有完整 gateway 时，`GET /api/readiness` 必须返回 `task_ready=false`，`POST /api/task` 必须返回 HTTP 503。
3. 每个平台都要同时填写 `*_API_ENDPOINT` 和 `*_API_KEY`。Shopping Agent 只把这两个网关配置交给后端适配器，平台原始凭证留在自有 gateway 内。
4. 启动后检查 `/api/health` 和 `/api/readiness`；后者必须逐项查看 `components`，不能把 `configured` 或 `configured_not_probed` 当作已连通。

### 故障与数据边界

`output/` 保存任务快照和报告，`uploaded/` 保存上传文件，`data/` 保存可选索引；Compose 对应命名卷为 `shopping_agent_output`、`shopping_agent_uploaded` 和 `shopping_agent_data`，Redis 与 OpenSearch 另有各自数据卷。`make compose-down` 只停止容器，不删除数据。`image_analysis=false` 仅表示当前没有图像理解能力，不能从“允许上传”推导出图像搜索能力。

Sandbox fixture 和 mixed source 只用于明确的 Sandbox/developer diagnostic 验收路径。普通 live 用户路径不得将 fixture 当作 gateway 结果；每个结果必须保留 `source`、状态、fallback 原因和数据模式。Anonymous Shopper ID 只是浏览器本地关联标识，不是 authentication、account、authorization 或所有权证明。

受控浏览器验收通过 `make browser-acceptance` 运行，使用仓库内的 deterministic backend，不需要真实平台或模型凭证；完整发布门禁是 `make verify`。

## 建议执行顺序

1. 先申请 eBay Developer keyset，用 Browse API 做第一个真实端到端 provider，并向 Support 确认生产比价用途。
2. 同步运营一个合规的 Amazon Associates 站点；满足销售门槛后申请 Creators API，但在获得 AI 分析数据流书面许可前不要上线 Amazon provider。
3. 根据实际目标市场申请 Shopee Affiliate Open API，不要用卖家接口假装全站搜索。
4. 先向 AliExpress Support 确认 2026 年可用产品，再决定是否开发；不要依赖已废弃教程。
5. 若采购第三方 API，先完成授权链和用途条款审查，再付费和接入。
