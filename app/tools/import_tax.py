from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.schemas import CustomsTaxEvidence, ImportTaxBreakdown

_CENT = Decimal("0.01")
_CBEC_POLICY_FACTOR = Decimal("0.70")


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def calculate_import_tax(
    evidence: CustomsTaxEvidence,
    *,
    product_price_cny: float,
    international_shipping_cny: float,
) -> ImportTaxBreakdown:
    """Calculate China import taxes from provider-supplied classification and rates."""

    customs_value = _money(
        _decimal(evidence.valuation.customs_value_cny)
        if evidence.valuation is not None
        else (
            _decimal(product_price_cny)
            + _decimal(international_shipping_cny)
            + _decimal(evidence.insurance_cny)
        )
    )
    tariff: Decimal | None = None
    import_vat: Decimal | None = None
    consumption_tax: Decimal | None = None
    tax_before_exemption: Decimal | None = None
    tax_exemption = Decimal("0.00")
    tax_exemption_reason: str | None = None
    policy_factor = Decimal(1)

    if evidence.import_regime == "general_trade":
        tariff_rate = _decimal(evidence.tariff_rate or 0)
        vat_rate = _decimal(evidence.import_vat_rate or 0)
        consumption_rate = _decimal(evidence.consumption_tax_rate)
        tariff = _money(customs_value * tariff_rate)
        consumption_tax = _money(
            (customs_value + tariff) / (Decimal(1) - consumption_rate) * consumption_rate
        )
        import_vat = _money((customs_value + tariff + consumption_tax) * vat_rate)
        calculation_method = "statutory_formula"
        calculation_basis = (
            "完税价格=商品价+国际运费+保险费；关税=完税价格×适用税率；"
            "消费税=（完税价格+关税）÷（1-消费税率）×消费税率；"
            "进口增值税=（完税价格+关税+消费税）×增值税率"
        )
        total = tariff + consumption_tax + import_vat
    elif evidence.import_regime == "cross_border_ecommerce":
        vat_rate = _decimal(evidence.import_vat_rate or 0)
        consumption_rate = _decimal(evidence.consumption_tax_rate)
        policy_factor = _CBEC_POLICY_FACTOR
        tax_base = customs_value / (Decimal(1) - consumption_rate)
        tariff = Decimal("0.00")
        consumption_tax = _money(tax_base * consumption_rate * policy_factor)
        import_vat = _money(tax_base * vat_rate * policy_factor)
        calculation_method = "cross_border_policy"
        calculation_basis = (
            "在数据通道明确确认清单与额度资格后，关税税率暂设为0%；"
            "进口环节增值税、消费税按法定应纳税额的70%征收；"
            "完税价格包含商品零售价、运费和保险费"
        )
        total = tariff + consumption_tax + import_vat
    elif evidence.import_regime == "personal_postal":
        customs_value = _money(_decimal(evidence.personal_postal_assessed_value_cny or 0))
        tax_before_exemption = _money(
            customs_value * _decimal(evidence.personal_postal_tax_rate or 0)
        )
        threshold = _money(_decimal(evidence.personal_postal_tax_exemption_threshold_cny or 0))
        if tax_before_exemption <= threshold:
            tax_exemption = tax_before_exemption
            total = Decimal("0.00")
            tax_exemption_reason = f"个人寄递物品应征税额不超过 ¥{threshold:,.2f}，免税放行"
        else:
            total = tax_before_exemption
        calculation_method = "personal_postal_rate"
        calculation_basis = (
            "海关核定计税价格×数据通道按物品分类返回的综合税率；"
            f"应征税额不超过¥{threshold:,.2f}时免税"
        )
    else:
        total = _money(_decimal(evidence.seller_collected_tax_cny or 0))
        calculation_method = "provider_quote"
        calculation_basis = "使用数据通道返回的卖家/结算页已代收进口税费报价，不拆分税种"

    return ImportTaxBreakdown(
        import_regime=evidence.import_regime,
        calculation_method=calculation_method,
        hs_code=evidence.hs_code,
        country_of_origin=evidence.country_of_origin,
        destination_country=evidence.destination_country,
        customs_value_cny=float(customs_value),
        customs_valuation=evidence.valuation,
        rate_type=evidence.rate_type,
        tariff_rate=evidence.tariff_rate,
        import_vat_rate=evidence.import_vat_rate,
        consumption_tax_rate=evidence.consumption_tax_rate,
        personal_postal_tax_rate=evidence.personal_postal_tax_rate,
        personal_postal_assessed_value_cny=evidence.personal_postal_assessed_value_cny,
        personal_postal_total_value_cny=evidence.personal_postal_total_value_cny,
        personal_postal_value_limit_cny=evidence.personal_postal_value_limit_cny,
        personal_postal_tax_exemption_threshold_cny=(
            evidence.personal_postal_tax_exemption_threshold_cny
        ),
        personal_postal_single_indivisible_item=(evidence.personal_postal_single_indivisible_item),
        policy_factor=float(policy_factor),
        tariff_cny=float(tariff) if tariff is not None else None,
        import_vat_cny=float(import_vat) if import_vat is not None else None,
        consumption_tax_cny=float(consumption_tax) if consumption_tax is not None else None,
        tax_before_exemption_cny=(
            float(tax_before_exemption) if tax_before_exemption is not None else None
        ),
        tax_exemption_cny=float(tax_exemption),
        tax_exemption_reason=tax_exemption_reason,
        total_import_tax_cny=float(_money(total)),
        provider=evidence.provider,
        source_reference=evidence.source_reference,
        effective_date=evidence.effective_date,
        calculation_basis=calculation_basis,
    )
