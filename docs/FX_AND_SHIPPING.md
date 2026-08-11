# 人民币换算与国际运费实现边界

> 核对日期：2026-08-11。汇率、承运商费率、燃油附加费和偏远地区附加费会变化；线上结果必须
> 使用数据提供商在本次研究中返回的报价证据，不能把本文示例数字当作费率表。

本项目面向中国大陆客户统一展示人民币到手价，但“统一展示”不等于承诺最终扣款金额。线上计算
只接受可追溯、带时间边界的商品级汇率与运费报价；缺少证据时保留商品原始数据并停止该候选的
到手价排名，不从平台名称、商品标题或默认重量猜测金额。

## 三种汇率必须分开

1. **研究比较汇率**：把数据通道返回的商品原币价格换算为 CNY，供同一次研究横向比较。
   每个非 CNY offer 必须携带 `price_conversion`。
2. **最终支付汇率**：由平台 DCC、收单机构、卡组织、发卡行、钱包及其费用共同决定。本项目只
   披露报价是否包含加价，不把研究比较汇率描述成最终扣款汇率。
3. **海关计税汇率**：用于形成申报月完税价格，与客户比较或支付汇率无关。一般贸易和跨境电商
   的 `customs.valuation.customs_conversion` 单独保存申报日期、计税月份、汇率依据和来源引用。

Visa 对动态货币转换的说明明确要求披露汇率和额外加价，客户可接受或拒绝 DCC；因此即使研究
阶段使用卡组织参考报价，也必须保留 `markup_status`，并提示最终支付以结算页和发卡行为准。
[Visa DCC](https://www.visa.com/en-us/personal/travel/dynamic-currency-conversion)、
[Visa Foreign Exchange API](https://developer.visa.com/capabilities/foreign_exchange/reference)

海关计税汇率按海关规则形成月度口径，不能拿实时支付报价替代。数据提供商应返回已经核验的当月
证据，Shopping Agent 只校验月份和计算链路，不自行抓取或猜测官方数值。
[中华人民共和国海关进出口货物征税管理办法](https://www.mct.gov.cn/preview/whhlyqyzcxxfw/ss/202411/t20241106_956082.html)

### 商品价格换算契约

```json
{
  "price": 129.99,
  "currency": "USD",
  "price_conversion": {
    "source_currency": "USD",
    "target_currency": "CNY",
    "rate_to_cny": 7.18,
    "purpose": "comparison_estimate",
    "rate_type": "provider_quote",
    "markup_status": "excluded",
    "markup_bps": null,
    "provider": "licensed-fx-feed",
    "source_reference": "fx-quote-20260811-001",
    "observed_at": "2026-08-11T01:30:00Z",
    "expires_at": "2026-08-11T02:00:00Z"
  }
}
```

- `rate_to_cny` 始终表示 1 单位 `source_currency` 可换算的人民币金额，禁止方向含糊的 `rate`。
- `rate_type` 可为数据商报价、卡组织估算或中间价参考；Sandbox 只使用显式 fixture 类型。
- `markup_status` 区分报价已含、不含或未知支付加价；仅在已含时填写 `markup_bps`。
- Live 非 CNY offer 必须有带时区的 `observed_at` 和尚未到期的 `expires_at`。
- `price_cny = price × rate_to_cny`，使用十进制定点运算并按人民币分四舍五入。
- CNY 原价按 1:1 进入比较，不伪造一条换汇报价。

部分候选缺少、错配或过期报价时，结果分别写入 `missing_fx_evidence` 或
`invalid_fx_evidence`；如果没有任何候选能完成换算，任务返回 `fx_rates_unavailable`。系统不读取
`FX_RATES_JSON`，也没有内置线上参考表。

## 运费必须是线路和服务报价

国际运输费用取决于始发地、目的地、运输服务、包裹件数、实际重量、体积重量、计费重量、账号价
以及燃油、偏远地区、旺季等附加费。线上路径要求数据提供商返回承运商或平台结算页的实际报价，
而不是使用“Amazon 固定多少钱”或“缺重量按 0.5kg”一类规则。

承运商通常按实际重量与体积重量中较高者确定计费基础，并可能再应用最低计费单位或服务规则。
Shopping Agent 不重算承运商价目表，只校验返回的 `chargeable_weight_kg` 不低于已知实际/体积
重量，并保存承运商使用的尺寸和除数。
[DHL 计费重量说明](https://www.dhl.com/discover/zh-cn/open-an-account-knowledge/chargeable-weight)

### 运费报价契约

```json
{
  "shipping_quote": {
    "quote_type": "carrier_quote",
    "currency": "USD",
    "total_amount": 14.50,
    "base_amount": 12.00,
    "surcharge_amount": 3.00,
    "discount_amount": 0.50,
    "actual_weight_kg": 1.20,
    "dimensional_weight_kg": 2.40,
    "chargeable_weight_kg": 2.40,
    "length_cm": 40,
    "width_cm": 30,
    "height_cm": 10,
    "dimensional_divisor": 5000,
    "origin_country": "US",
    "destination_country": "CN",
    "service_name": "International Priority",
    "eta_min_days": 6,
    "eta_max_days": 9,
    "provider": "licensed-carrier-rate-feed",
    "source_reference": "shipping-quote-20260811-001",
    "observed_at": "2026-08-11T01:31:00Z",
    "expires_at": "2026-08-11T02:00:00Z",
    "currency_conversion": {
      "source_currency": "USD",
      "target_currency": "CNY",
      "rate_to_cny": 7.18,
      "purpose": "comparison_estimate",
      "rate_type": "provider_quote",
      "markup_status": "excluded",
      "markup_bps": null,
      "provider": "licensed-fx-feed",
      "source_reference": "fx-quote-20260811-002",
      "observed_at": "2026-08-11T01:30:00Z",
      "expires_at": "2026-08-11T02:00:00Z"
    }
  }
}
```

- `quote_type` 区分承运商报价、平台结算页报价、已含运费和 Sandbox fixture。
- `total_amount = base_amount + surcharge_amount - discount_amount`；提供分项时必须对账到分。
- 报价必须指向中国大陆，保留始发地、服务名、报价引用、观测时间、有效期和 ETA 区间。
- 外币运费必须携带同币种的 `currency_conversion`，并与主商品价格报价分别留痕。
- 排名采用 `eta_max_days`，避免把乐观区间下限当作承诺时效。
- `shipping_cny = total_amount × 运费报价汇率`，按人民币分四舍五入。

缺少报价、报价结构无效或报价过期时，候选分别进入 `missing_shipping_quote`、
`invalid_shipping_quote` 或 `expired_shipping_quote`，不进入到手价排名。DHL 和 FedEx 的官方
Rate/Quote API 也都以路线、包裹和服务参数返回时点报价，说明生产系统应保存报价响应，而不是把
页面价格表复制成代码常量。
[DHL Price Quote API](https://developer.dhl.com/api-reference/price-quote-dhl-freight)、
[FedEx Rate API](https://developer.fedex.com/api/en-wf/catalog/rate.html)

## 到手价与失效边界

```text
商品价 CNY = 原币商品价 × 商品比较汇率
运费 CNY = 原币运费报价 × 运费比较汇率
到手价 CNY = 商品价 CNY + 运费 CNY + 客户承担的保险费 + 进口税费
```

所有金额使用十进制定点运算并按人民币分四舍五入。商品报价、汇率、运费、海关估价和税率各自
保留来源与时间，不用一个全局时间戳掩盖不同数据时点。结果和报告必须提示：最终商品价、支付
汇率、支付机构费用、承运费用、进口税费和时效以平台结算页、发卡行、承运商及海关核定为准。

Sandbox 可使用确定性 fixture，但每条 fixture 仍要走同一 typed contract，并明确标记
`sandbox_fixture`。生产环境不得把 fixture、静态汇率表或平台运费常量作为 fallback。
