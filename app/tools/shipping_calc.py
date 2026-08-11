from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.schemas import (
    EstimateDisclosure,
    LandedCost,
    PricePoint,
    ShippingCalcOutput,
    ShippingCalculationExclusion,
    TaxCalculationExclusion,
)
from app.tools.destination import (
    SUPPORTED_DESTINATION,
    UnsupportedDestinationError,
    normalize_destination,
)
from app.tools.import_tax import calculate_import_tax

_CNY_CENT = Decimal("0.01")


def _money(amount: float, rate: float = 1) -> float:
    return float(
        (Decimal(str(amount)) * Decimal(str(rate))).quantize(
            _CNY_CENT,
            rounding=ROUND_HALF_UP,
        )
    )


def _shipping_basis(item: PricePoint) -> str:
    quote = item.shipping_quote
    if quote is None:  # Guarded by the caller; keeps the helper total for type checking.
        return "运费报价不可用"
    components = [f"{quote.service_name} 报价 {quote.currency} {quote.total_amount:.2f}"]
    if quote.base_amount is not None:
        components.append(f"基础运费 {quote.currency} {quote.base_amount:.2f}")
        components.append(f"附加费 {quote.currency} {quote.surcharge_amount:.2f}")
        components.append(f"优惠 {quote.currency} {quote.discount_amount:.2f}")
    if quote.chargeable_weight_kg is not None:
        components.append(f"计费重量 {quote.chargeable_weight_kg:.2f}kg")
    if quote.dimensional_weight_kg is not None:
        components.append(f"体积重量 {quote.dimensional_weight_kg:.2f}kg")
    components.append(f"时效 {quote.eta_min_days}-{quote.eta_max_days} 天")
    return "；".join(components)


async def shipping_calc(
    items: list[PricePoint],
    destination: str = "中国大陆",
    *,
    calculated_at: datetime | None = None,
) -> ShippingCalcOutput:
    """Use provider shipping quotes and calculate taxes from typed customs evidence."""

    destination = normalize_destination(destination)
    if destination != SUPPORTED_DESTINATION:
        raise UnsupportedDestinationError(destination)
    calculation_time = calculated_at or datetime.now(timezone.utc)
    if calculation_time.tzinfo is None or calculation_time.utcoffset() is None:
        raise ValueError("calculated_at must include a timezone")
    calculation_time = calculation_time.astimezone(timezone.utc)

    landed: list[LandedCost] = []
    shipping_exclusions: list[ShippingCalculationExclusion] = []
    tax_exclusions: list[TaxCalculationExclusion] = []
    for item in items[:12]:
        if item.shipping_quote is None:
            shipping_exclusions.append(
                ShippingCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="missing_shipping_quote",
                    reason=(
                        "数据通道未提供面向中国大陆、包含运输服务和时效的运费报价，"
                        "无法生成可参与排序的到手价。"
                    ),
                )
            )
            continue
        quote = item.shipping_quote
        if item.source == "live" and quote.expires_at is None:
            shipping_exclusions.append(
                ShippingCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="invalid_shipping_quote",
                    reason="线上运费报价未提供有效期，无法参与到手价排序。",
                )
            )
            continue
        if (
            quote.expires_at is not None
            and datetime.fromisoformat(quote.expires_at.replace("Z", "+00:00")) <= calculation_time
        ):
            shipping_exclusions.append(
                ShippingCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="expired_shipping_quote",
                    reason=f"运费报价已过期（有效期至 {quote.expires_at}），无法参与到手价排序。",
                )
            )
            continue
        conversion = quote.currency_conversion
        if item.source == "live" and conversion is not None and conversion.expires_at is None:
            shipping_exclusions.append(
                ShippingCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="invalid_shipping_quote",
                    reason="线上外币运费报价的人民币换算证据未提供有效期，无法参与到手价排序。",
                )
            )
            continue
        if (
            conversion is not None
            and conversion.expires_at is not None
            and datetime.fromisoformat(conversion.expires_at.replace("Z", "+00:00"))
            <= calculation_time
        ):
            shipping_exclusions.append(
                ShippingCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="invalid_shipping_quote",
                    reason="运费报价所用的人民币换算证据已过期，无法参与到手价排序。",
                )
            )
            continue
        shipping = _money(
            quote.total_amount,
            conversion.rate_to_cny if conversion is not None else 1,
        )
        eta = quote.eta_max_days
        if item.customs is None:
            tax_exclusions.append(
                TaxCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="missing_customs_evidence",
                    reason=(
                        "数据通道未提供 HS Code、原产地、进口模式及带生效日期的税率证据，"
                        "无法生成可参与排序的到手价。"
                    ),
                )
            )
            continue
        if (
            item.customs.import_regime in {"general_trade", "cross_border_ecommerce"}
            and item.customs.valuation is None
        ):
            tax_exclusions.append(
                TaxCalculationExclusion(
                    item_id=item.item_id,
                    platform=item.platform,
                    title=item.title,
                    reason_code="missing_customs_valuation",
                    reason=(
                        "数据通道未提供独立于客户比价汇率的海关月度汇率与 CIF 完税价格明细，"
                        "无法生成可参与排序的进口税费。"
                    ),
                )
            )
            continue
        tax = calculate_import_tax(
            item.customs,
            product_price_cny=item.price_cny,
            international_shipping_cny=shipping,
        )
        effective_rate = (
            tax.total_import_tax_cny / tax.customs_value_cny if tax.customs_value_cny else 0
        )
        tier = (
            "免征"
            if tax.total_import_tax_cny == 0
            else ("高税" if effective_rate >= 0.2 else "标准")
        )
        item_data = item.model_dump()
        item_data["note"] = item.note
        tax_disclosure = EstimateDisclosure(
            estimated=True,
            source=item.customs.provider,
            calculation_basis=tax.calculation_basis,
        )
        landed.append(
            LandedCost(
                **item_data,
                shipping_cny=shipping,
                insurance_cny=item.customs.insurance_cny,
                duty_cny=tax.tariff_cny,
                import_vat_cny=tax.import_vat_cny,
                consumption_tax_cny=tax.consumption_tax_cny,
                import_tax_cny=tax.total_import_tax_cny,
                landed_cny=_money(
                    item.price_cny
                    + shipping
                    + item.customs.insurance_cny
                    + tax.total_import_tax_cny,
                ),
                eta_days=eta,
                duty_tier=tier,
                shipping_estimate=EstimateDisclosure(
                    estimated=True,
                    source=quote.provider,
                    calculation_basis=_shipping_basis(item),
                ),
                duty_estimate=EstimateDisclosure(
                    estimated=True,
                    source=item.customs.provider,
                    calculation_basis=tax.calculation_basis,
                ),
                tax_estimate=tax_disclosure,
                delivery_estimate=EstimateDisclosure(
                    estimated=True,
                    source=quote.provider,
                    calculation_basis=(
                        f"{quote.service_name} 报价时效 {quote.eta_min_days}-"
                        f"{quote.eta_max_days} 天；排序采用上限"
                    ),
                ),
                tax_breakdown=tax,
            )
        )
    landed.sort(key=lambda item: (item.landed_cny, item.platform, item.item_id))
    return ShippingCalcOutput(
        destination=destination,
        items=landed,
        calculation_basis=(
            "运费与配送时效使用数据通道返回的线路/服务报价，金额包含已披露的附加费和优惠，"
            "计费重量取承运商 quote；进口税费按数据通道提供的 HS Code、原产地、进口模式和"
            "带生效日期的税率证据计算"
        ),
        shipping_exclusions=shipping_exclusions,
        tax_exclusions=tax_exclusions,
    )
