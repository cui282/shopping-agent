from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import unquote_plus

import pytest

from app.memory.injector import merge_preference_records
from app.schemas import (
    Candidate,
    CurrencyConversionEvidence,
    CustomsExchangeRateEvidence,
    CustomsTaxEvidence,
    CustomsValuationEvidence,
    ItemPickerOutput,
    OfferProvenance,
    ProductIdentity,
    ShippingCalculationExclusion,
    ShippingQuoteEvidence,
    TaxCalculationExclusion,
)
from app.tools.category_insight import category_insight
from app.tools.item_picker import item_picker
from app.tools.item_search import _parse_live_item, item_search
from app.tools.planner import planner
from app.tools.price_compare import MissingExchangeRatesError, price_compare
from app.tools.query_parser import extract_product_subject
from app.tools.shipping_calc import shipping_calc
from app.tools.shopping_summary import shopping_summary
from app.utils.thread_ctx import thread_scope


def _live_fx_quote(currency: str = "USD", rate: float = 7.18) -> CurrencyConversionEvidence:
    return CurrencyConversionEvidence(
        source_currency=currency,
        rate_to_cny=rate,
        rate_type="provider_quote",
        provider="licensed-fx-feed",
        source_reference=f"fx/{currency.lower()}-cny-test-quote",
        observed_at="2026-08-11T01:00:00Z",
        expires_at="2099-01-01T00:00:00Z",
    )


def _live_shipping_quote(
    amount_cny: float,
    *,
    origin_country: str = "US",
    eta_days: int = 12,
    weight_kg: float | None = 0.5,
    quote_type: str = "carrier_quote",
) -> ShippingQuoteEvidence:
    return ShippingQuoteEvidence(
        quote_type=quote_type,
        currency="CNY",
        total_amount=amount_cny,
        base_amount=amount_cny,
        actual_weight_kg=weight_kg,
        chargeable_weight_kg=weight_kg,
        origin_country=origin_country,
        destination_country="CN",
        service_name="Test international service",
        eta_min_days=max(0, eta_days - 2),
        eta_max_days=eta_days,
        provider="licensed-carrier-rate-feed",
        source_reference="shipping/test-quote",
        observed_at="2026-08-11T01:00:00Z",
        expires_at="2099-01-01T00:00:00Z",
    )


def _customs_valuation(
    *,
    goods_value_original: float,
    goods_currency: str,
    goods_value_cny: float,
    shipping_cny: float,
    insurance_cny: float = 0,
    customs_rate: float | None = None,
) -> CustomsValuationEvidence:
    conversion = (
        CustomsExchangeRateEvidence(
            source_currency=goods_currency,
            rate_to_cny=customs_rate or 1,
            declaration_date="2026-08-11",
            assessment_month="2026-08",
            provider="licensed-customs-fx-feed",
            source_reference="customs/monthly-rate/2026-08",
        )
        if goods_currency != "CNY"
        else None
    )
    return CustomsValuationEvidence(
        goods_value_original=goods_value_original,
        goods_currency=goods_currency,
        goods_value_cny=goods_value_cny,
        international_shipping_cny=shipping_cny,
        insurance_cny=insurance_cny,
        customs_value_cny=round(goods_value_cny + shipping_cny + insurance_cny, 2),
        customs_conversion=conversion,
        provider="licensed-customs-valuation-feed",
        source_reference="valuation/test-cif",
    )


@pytest.mark.asyncio
async def test_planner_extracts_budget_category_and_constraints() -> None:
    plan = await planner("预算 1200 元，找轻便降噪耳机，不要皮革，寄到上海")
    assert plan.budget_cny == 1200
    assert plan.category == "耳机"
    assert "轻便" in plan.soft_preferences
    assert "不含皮革" in plan.material_preferences
    assert plan.destination == "中国大陆"


@pytest.mark.asyncio
async def test_planner_builds_explicit_ranking_profile() -> None:
    plan = await planner("找耳机，优先配送速度，其次偏好匹配，再看价格")

    assert plan.ranking_profile.explicit is True
    assert plan.ranking_profile.priority_order == [
        "delivery_time",
        "preference_match",
        "landed_cost",
        "evidence_quality",
    ]


@pytest.mark.asyncio
async def test_planner_normalizes_product_research_and_exact_offer_modes() -> None:
    product_research = await planner("比较不同产品的降噪耳机，重点看舒适度")
    exact_comparison = await planner("比价同款降噪耳机，只比较相同型号")

    assert product_research.mode == "product_research"
    assert exact_comparison.mode == "exact_offer_comparison"


@pytest.mark.asyncio
async def test_planner_normalizes_mainland_addresses_and_preserves_unsupported_destinations() -> (
    None
):
    mainland = await planner("找耳机，配送至深圳市南山区")
    unsupported = await planner("找耳机，收货地址为香港")

    assert mainland.destination == "中国大陆"
    assert unsupported.destination == "香港"


@pytest.mark.asyncio
async def test_unlisted_product_subject_is_preserved_through_sandbox_search(monkeypatch) -> None:
    query = "预算2000元，找一款适合露营的便携电源，重点看重量和充电速度"
    plan = await planner(query)

    assert plan.category == "便携电源"

    monkeypatch.setenv("SANDBOX_MODE", "true")
    result = await item_search(query, "aliexpress", top_k=2)
    assert result.candidates
    assert all("便携电源" in item.title for item in result.candidates)
    assert all("便携电源" in unquote_plus(item.product_url or "") for item in result.candidates)

    overlapping_feature = await planner("不要耳机，比较降噪音箱，预算800元")
    assert overlapping_feature.category == "降噪音箱"

    attribute_comparison = await item_search("比较便携电源的重量和充电速度", "shopee", top_k=1)
    assert "便携电源" in attribute_comparison.candidates[0].title


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("找一个手机壳，预算100元", "手机壳"),
        ("给手机买个充电器", "充电器"),
        ("找跑鞋清洁剂", "跑鞋清洁剂"),
        ("帮我找一款便携电源", "便携电源"),
        ("买个扫地机器人", "扫地机器人"),
        ("我需要适合宿舍的电煮锅", "电煮锅"),
        ("找一根 USB-C 数据线", "USB-C 数据线"),
        ("预算5000元买手机", "手机"),
        ("比较便携电源的重量和充电速度", "便携电源"),
        ("对比扫地机器人的避障和拖地能力", "扫地机器人"),
        ("帮我比价一款扫地机器人", "扫地机器人"),
        ("比较咖啡机滤纸的价格", "咖啡机滤纸"),
        ("找小米的充电宝", "充电宝"),
        ("找大功率的充电器", "充电器"),
        ("找无线的降噪耳机", "耳机"),
        ("比较显示器的刷新率和色准", "显示器"),
    ],
)
def test_product_subject_parser_handles_common_request_phrasing(query: str, expected: str) -> None:
    assert extract_product_subject(query) == expected


@pytest.mark.asyncio
async def test_sandbox_search_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "true")
    result = await item_search("降噪耳机", "amazon", top_k=2)
    assert result.provider.source == "fixture"
    assert result.provider.status == "degraded"
    assert result.provider.fallback_reason
    assert len(result.candidates) == 2
    assert all(item.source == "fixture" for item in result.candidates)


@pytest.mark.asyncio
async def test_failed_live_provider_is_not_reported_as_success(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "false")
    monkeypatch.setenv("AMAZON_DATA_PROVIDER", "purchased-catalog-provider")
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_ENDPOINT", "http://127.0.0.1:1/search")
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_CREDENTIAL", "test-credential")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "1")

    result = await item_search("降噪耳机", "amazon", top_k=2)

    assert result.candidates == []
    assert result.provider.source == "live"
    assert result.provider.provider == "purchased-catalog-provider"
    assert result.provider.status == "unavailable"
    assert result.provider.fallback_reason
    assert result.provider.failure_reason == "request_failed"


@pytest.mark.asyncio
async def test_blank_live_gateway_is_reported_as_not_configured(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "   ")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")

    result = await item_search("降噪耳机", "amazon", top_k=2)

    assert result.candidates == []
    assert result.provider.failure_reason == "not_configured"


@pytest.mark.asyncio
async def test_fixture_fallback_requires_explicit_developer_diagnostic_mode(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_MODE", "false")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "http://127.0.0.1:1/search")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "1")

    normal = await item_search("降噪耳机", "amazon", top_k=2)
    assert normal.candidates == []
    assert normal.provider.source == "live"
    assert normal.provider.failure_reason == "request_failed"

    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_MODE", "true")
    diagnostic = await item_search("降噪耳机", "amazon", top_k=2)
    assert diagnostic.candidates
    assert diagnostic.provider.source == "fixture"
    assert diagnostic.provider.failure_reason == "request_failed"


@pytest.mark.asyncio
async def test_production_sandbox_fails_closed_at_provider_boundary(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SANDBOX_MODE", "true")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "")
    monkeypatch.setenv("AMAZON_API_KEY", "")

    result = await item_search("降噪耳机", "amazon", top_k=2)

    assert result.candidates == []
    assert result.provider.source == "live"
    assert result.provider.status == "unavailable"
    assert result.provider.failure_reason == "sandbox_forbidden"


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-Infinity"])
def test_live_parser_rejects_non_finite_prices(price: str) -> None:
    assert (
        _parse_live_item(
            {"id": "invalid", "title": "Invalid price", "price": price, "currency": "USD"},
            "amazon",
        )
        is None
    )


@pytest.mark.asyncio
async def test_price_shipping_and_picker_preserve_product_fields() -> None:
    candidate = Candidate(
        item_id="one",
        platform="amazon",
        title="轻量耳机",
        price=100,
        currency="USD",
        price_conversion=_live_fx_quote(),
        rating=4.8,
        sales=200,
        image_url="https://example.com/image.jpg",
        product_url="https://example.com/item",
        attributes={"weight_kg": 0.34, "material": "织物"},
        shipping_quote=_live_shipping_quote(85, origin_country="CN", weight_kg=0.34),
        offer_id="provider-offer-one",
        identity=ProductIdentity(gtin="4006381333931", mpn="ACME-X1", brand="Acme", model="X1"),
        variant_attributes={"capacity": "256 GB", "condition": "new"},
        availability="in_stock",
        retrieved_at="2026-07-30T10:00:00Z",
        provenance=OfferProvenance(
            kind="marketplace_gateway",
            provider="licensed-feed",
            upstream_source="provider-catalog",
        ),
        link_kind="product_detail",
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="CN",
            destination_country="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0.10,
            import_vat_rate=0.13,
            consumption_tax_rate=0,
            insurance_cny=0,
            valuation=_customs_valuation(
                goods_value_original=100,
                goods_currency="USD",
                goods_value_cny=718,
                shipping_cny=85,
                customs_rate=7.18,
            ),
            provider="licensed-customs-feed",
            source_reference="CN tariff snapshot 2026",
            effective_date="2026-01-01",
        ),
        source="live",
    )
    prices = await price_compare([candidate])
    assert prices.ranked[0].price_cny == 718
    assert prices.exchange_rate.source == "offer-level-quotes"
    assert prices.exchange_rate.effective_date != "unspecified"
    assert prices.exchange_rate.calculation_basis == "original_amount * rate_to_cny"
    shipping = await shipping_calc(prices.ranked)
    item = shipping.items[0]
    assert item.shipping_cny == 85
    assert item.duty_cny == pytest.approx(80.3)
    assert item.import_vat_cny == pytest.approx(114.83)
    assert item.import_tax_cny == pytest.approx(195.13)
    assert item.tax_breakdown.hs_code == "8518300000"
    assert item.tax_breakdown.country_of_origin == "CN"
    assert item.tax_breakdown.rate_type == "mfn"
    assert item.shipping_estimate.estimated is True
    assert item.shipping_estimate.source == "licensed-carrier-rate-feed"
    assert item.duty_estimate.estimated is True
    assert item.delivery_estimate.estimated is True
    assert item.image_url == candidate.image_url
    plan = await planner("预算 1000 元，找轻便耳机，不要皮革")
    picks = await item_picker(shipping, plan)
    assert picks.recommendations[0].item_id == "one"
    assert picks.recommendations[0].rank == 1
    assert picks.recommendations[0].offer_id == "provider-offer-one"
    assert picks.recommendations[0].identity == candidate.identity
    assert picks.recommendations[0].variant_attributes == candidate.variant_attributes
    assert picks.recommendations[0].availability == "in_stock"
    assert picks.recommendations[0].retrieved_at == "2026-07-30T10:00:00Z"
    assert picks.recommendations[0].provenance == candidate.provenance
    assert picks.recommendations[0].link_kind == "product_detail"


@pytest.mark.asyncio
async def test_checkout_shipping_quote_does_not_invent_weight() -> None:
    candidate = Candidate(
        item_id="unknown-weight",
        platform="ebay",
        title="Provider item",
        price=50,
        currency="USD",
        price_conversion=_live_fx_quote(),
        attributes={},
        shipping_quote=_live_shipping_quote(
            150,
            origin_country="US",
            eta_days=12,
            weight_kg=None,
            quote_type="marketplace_checkout",
        ),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="CN",
            destination_country="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0,
            import_vat_rate=0.13,
            valuation=_customs_valuation(
                goods_value_original=50,
                goods_currency="USD",
                goods_value_cny=359,
                shipping_cny=150,
                customs_rate=7.18,
            ),
            provider="licensed-customs-feed",
            source_reference="CN tariff snapshot 2026",
            effective_date="2026-01-01",
        ),
        source="live",
    )
    prices = await price_compare([candidate])
    shipping = await shipping_calc(prices.ranked)
    assert shipping.items[0].note is None
    assert shipping.items[0].shipping_quote.chargeable_weight_kg is None
    assert "计费重量" not in shipping.items[0].shipping_estimate.calculation_basis


@pytest.mark.asyncio
async def test_general_trade_uses_hs_origin_and_statutory_tax_components() -> None:
    candidate = Candidate(
        item_id="general-trade",
        platform="amazon",
        title="General trade item",
        price=1000,
        currency="CNY",
        attributes={"weight_kg": 0.3},
        shipping_quote=_live_shipping_quote(85, origin_country="FR", weight_kg=0.3),
        customs=CustomsTaxEvidence(
            hs_code="3303000010",
            country_of_origin="FR",
            destination_country="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0.10,
            import_vat_rate=0.13,
            consumption_tax_rate=0.20,
            insurance_cny=15,
            valuation=_customs_valuation(
                goods_value_original=1000,
                goods_currency="CNY",
                goods_value_cny=1000,
                shipping_cny=85,
                insurance_cny=15,
            ),
            provider="licensed-customs-feed",
            source_reference="CN tariff snapshot 2026",
            effective_date="2026-01-01",
        ),
        source="live",
    )

    shipping = await shipping_calc(
        (await price_compare([candidate])).ranked,
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )

    item = shipping.items[0]
    assert item.tax_breakdown.customs_value_cny == 1100
    assert item.duty_cny == 110
    assert item.consumption_tax_cny == pytest.approx(302.5)
    assert item.import_vat_cny == pytest.approx(196.63)
    assert item.import_tax_cny == pytest.approx(609.13)
    assert item.landed_cny == pytest.approx(1709.13)


@pytest.mark.asyncio
async def test_cross_border_ecommerce_applies_policy_factor_only_when_explicitly_eligible() -> None:
    candidate = Candidate(
        item_id="cross-border",
        platform="ebay",
        title="Cross-border retail item",
        price=1000,
        currency="CNY",
        attributes={"weight_kg": 0.3},
        shipping_quote=_live_shipping_quote(90, origin_country="FR", weight_kg=0.3),
        customs=CustomsTaxEvidence(
            hs_code="3303000010",
            country_of_origin="FR",
            destination_country="CN",
            import_regime="cross_border_ecommerce",
            rate_type="cross_border_policy",
            tariff_rate=0,
            import_vat_rate=0.13,
            consumption_tax_rate=0.20,
            insurance_cny=10,
            valuation=_customs_valuation(
                goods_value_original=1000,
                goods_currency="CNY",
                goods_value_cny=1000,
                shipping_cny=90,
                insurance_cny=10,
            ),
            cross_border_ecommerce_eligible=True,
            provider="licensed-customs-feed",
            source_reference="CBEC positive-list eligibility quote",
            effective_date="2026-01-01",
        ),
        source="live",
    )

    shipping = await shipping_calc((await price_compare([candidate])).ranked)

    item = shipping.items[0]
    assert item.tax_breakdown.customs_value_cny == 1100
    assert item.duty_cny == 0
    assert item.consumption_tax_cny == pytest.approx(192.5)
    assert item.import_vat_cny == pytest.approx(125.13)
    assert item.import_tax_cny == pytest.approx(317.63)
    assert item.tax_breakdown.policy_factor == 0.70


@pytest.mark.asyncio
async def test_seller_collected_tax_uses_provider_quote_without_platform_inference() -> None:
    candidate = Candidate(
        item_id="seller-collected",
        platform="shopee",
        title="Seller-collected item",
        price=1000,
        currency="CNY",
        attributes={"weight_kg": 0.3},
        shipping_quote=_live_shipping_quote(35, origin_country="MY", eta_days=9, weight_kg=0.3),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="MY",
            destination_country="CN",
            import_regime="seller_collected",
            rate_type="provider_quote",
            seller_collected_tax_cny=168.5,
            provider="licensed-customs-feed",
            source_reference="checkout tax quote q-123",
            effective_date="2026-08-11",
        ),
        source="live",
    )

    shipping = await shipping_calc((await price_compare([candidate])).ranked)

    item = shipping.items[0]
    assert item.duty_cny is None
    assert item.import_vat_cny is None
    assert item.import_tax_cny == 168.5
    assert item.tax_breakdown.calculation_method == "provider_quote"


@pytest.mark.asyncio
async def test_personal_postal_uses_provider_classification_rate() -> None:
    candidate = Candidate(
        item_id="personal-postal",
        platform="shopee",
        title="Personal postal item",
        price=1000,
        currency="CNY",
        attributes={"weight_kg": 0.3},
        shipping_quote=_live_shipping_quote(35, origin_country="MY", eta_days=9, weight_kg=0.3),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="MY",
            destination_country="CN",
            import_regime="personal_postal",
            rate_type="personal_postal",
            personal_postal_tax_rate=0.20,
            personal_postal_assessed_value_cny=1000,
            personal_postal_total_value_cny=1000,
            personal_postal_value_limit_cny=2000,
            personal_postal_tax_exemption_threshold_cny=50,
            personal_postal_single_indivisible_item=False,
            personal_postal_eligible=True,
            insurance_cny=15,
            provider="licensed-customs-feed",
            source_reference="personal postal classification quote",
            effective_date="2026-08-11",
        ),
        source="live",
    )

    shipping = await shipping_calc((await price_compare([candidate])).ranked)

    item = shipping.items[0]
    assert item.tax_breakdown.customs_value_cny == 1000
    assert item.duty_cny is None
    assert item.import_tax_cny == 200
    assert item.landed_cny == 1250
    assert item.tax_breakdown.calculation_method == "personal_postal_rate"
    assert item.tax_breakdown.tax_before_exemption_cny == 200
    assert item.tax_breakdown.tax_exemption_cny == 0
    assert item.tax_breakdown.tax_exemption_reason is None


@pytest.mark.asyncio
async def test_personal_postal_applies_current_tax_amount_exemption() -> None:
    candidate = Candidate(
        item_id="personal-postal-exempt",
        platform="shopee",
        title="Low-value personal postal item",
        price=250,
        currency="CNY",
        attributes={"weight_kg": 0.3},
        shipping_quote=_live_shipping_quote(35, origin_country="MY", eta_days=9, weight_kg=0.3),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="MY",
            destination_country="CN",
            import_regime="personal_postal",
            rate_type="personal_postal",
            personal_postal_tax_rate=0.20,
            personal_postal_assessed_value_cny=250,
            personal_postal_total_value_cny=250,
            personal_postal_value_limit_cny=2000,
            personal_postal_tax_exemption_threshold_cny=50,
            personal_postal_single_indivisible_item=False,
            personal_postal_eligible=True,
            provider="licensed-customs-feed",
            source_reference="personal postal classification quote",
            effective_date="2026-08-11",
        ),
        source="live",
    )

    shipping = await shipping_calc((await price_compare([candidate])).ranked)

    item = shipping.items[0]
    assert item.tax_breakdown.customs_value_cny == 250
    assert item.tax_breakdown.tax_before_exemption_cny == 50
    assert item.tax_breakdown.tax_exemption_cny == 50
    assert item.tax_breakdown.tax_exemption_reason == "个人寄递物品应征税额不超过 ¥50.00，免税放行"
    assert item.import_tax_cny == 0
    assert item.landed_cny == 285


def test_personal_postal_over_value_limit_requires_indivisible_single_item() -> None:
    with pytest.raises(ValueError, match="single indivisible item"):
        CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="MY",
            destination_country="CN",
            import_regime="personal_postal",
            rate_type="personal_postal",
            personal_postal_tax_rate=0.20,
            personal_postal_assessed_value_cny=2500,
            personal_postal_total_value_cny=2500,
            personal_postal_value_limit_cny=2000,
            personal_postal_tax_exemption_threshold_cny=50,
            personal_postal_single_indivisible_item=False,
            personal_postal_eligible=True,
            provider="licensed-customs-feed",
            source_reference="personal postal classification quote",
            effective_date="2026-08-11",
        )


def test_customs_evidence_rejects_non_calendar_effective_date() -> None:
    with pytest.raises(ValueError, match="effective_date"):
        CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0,
            import_vat_rate=0.13,
            provider="licensed-customs-feed",
            source_reference="invalid date fixture",
            effective_date="2026-99-99",
        )


@pytest.mark.asyncio
async def test_missing_customs_evidence_is_excluded_from_landed_cost_ranking() -> None:
    candidate = Candidate(
        item_id="missing-tax-evidence",
        platform="aliexpress",
        title="Unclassified item",
        price=100,
        currency="CNY",
        shipping_quote=_live_shipping_quote(20, origin_country="CN", eta_days=25),
        source="live",
    )

    shipping = await shipping_calc((await price_compare([candidate])).ranked)

    assert shipping.items == []
    assert [item.reason_code for item in shipping.tax_exclusions] == ["missing_customs_evidence"]
    assert shipping.tax_exclusions[0].item_id == "missing-tax-evidence"


@pytest.mark.asyncio
async def test_live_offer_without_shipping_quote_is_excluded_from_landed_cost_ranking() -> None:
    candidate = Candidate(
        item_id="missing-shipping-quote",
        platform="amazon",
        title="Offer without a shipping quote",
        price=100,
        currency="CNY",
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="CN",
            destination_country="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0,
            import_vat_rate=0.13,
            provider="licensed-customs-feed",
            source_reference="CN tariff snapshot 2026",
            effective_date="2026-01-01",
        ),
        source="live",
    )

    shipping = await shipping_calc((await price_compare([candidate])).ranked)

    assert shipping.items == []
    assert [item.reason_code for item in shipping.shipping_exclusions] == ["missing_shipping_quote"]
    assert shipping.shipping_exclusions[0].item_id == "missing-shipping-quote"


@pytest.mark.asyncio
async def test_shipping_calc_uses_provider_quote_chargeable_weight_and_surcharges() -> None:
    quote = ShippingQuoteEvidence(
        quote_type="carrier_quote",
        currency="CNY",
        total_amount=90,
        base_amount=80,
        surcharge_amount=15,
        discount_amount=5,
        actual_weight_kg=1.2,
        dimensional_weight_kg=2.4,
        chargeable_weight_kg=2.4,
        length_cm=40,
        width_cm=30,
        height_cm=10,
        dimensional_divisor=5000,
        origin_country="US",
        destination_country="CN",
        service_name="International Priority",
        eta_min_days=6,
        eta_max_days=9,
        provider="licensed-carrier-rate-feed",
        source_reference="shipping/quote-123",
        observed_at="2026-08-11T01:00:00Z",
        expires_at="2026-08-11T02:00:00Z",
    )
    candidate = Candidate(
        item_id="quoted-shipping",
        platform="amazon",
        title="Offer with carrier quote",
        price=1000,
        currency="CNY",
        shipping_quote=quote,
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="US",
            destination_country="CN",
            import_regime="seller_collected",
            rate_type="provider_quote",
            seller_collected_tax_cny=0,
            provider="licensed-customs-feed",
            source_reference="checkout/tax-123",
            effective_date="2026-08-11",
        ),
        source="live",
    )

    shipping = await shipping_calc(
        (await price_compare([candidate])).ranked,
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )

    item = shipping.items[0]
    assert item.shipping_cny == 90
    assert item.eta_days == 9
    assert item.shipping_quote == quote
    assert item.shipping_estimate.source == "licensed-carrier-rate-feed"
    assert "计费重量 2.40kg" in item.shipping_estimate.calculation_basis
    assert "附加费 CNY 15.00" in item.shipping_estimate.calculation_basis


@pytest.mark.asyncio
async def test_live_shipping_quote_without_validity_window_is_excluded() -> None:
    candidate = Candidate(
        item_id="shipping-without-expiry",
        platform="amazon",
        title="Offer with no shipping validity window",
        price=1000,
        currency="CNY",
        shipping_quote=ShippingQuoteEvidence(
            quote_type="carrier_quote",
            currency="CNY",
            total_amount=90,
            base_amount=90,
            origin_country="US",
            destination_country="CN",
            service_name="International Priority",
            eta_min_days=6,
            eta_max_days=9,
            provider="licensed-carrier-rate-feed",
            source_reference="shipping/no-expiry",
            observed_at="2026-08-11T01:00:00Z",
        ),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="US",
            destination_country="CN",
            import_regime="seller_collected",
            rate_type="provider_quote",
            seller_collected_tax_cny=0,
            provider="licensed-customs-feed",
            source_reference="checkout/tax-no-expiry",
            effective_date="2026-08-11",
        ),
        source="live",
    )

    shipping = await shipping_calc(
        (await price_compare([candidate])).ranked,
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )

    assert shipping.items == []
    assert shipping.shipping_exclusions[0].reason_code == "invalid_shipping_quote"
    assert "有效期" in shipping.shipping_exclusions[0].reason


@pytest.mark.asyncio
async def test_customs_tax_uses_customs_monthly_fx_not_comparison_fx() -> None:
    candidate = Candidate(
        item_id="separate-customs-fx",
        platform="amazon",
        title="USD offer with separate customs valuation",
        price=100,
        currency="USD",
        price_conversion=CurrencyConversionEvidence(
            source_currency="USD",
            rate_to_cny=7.20,
            rate_type="provider_quote",
            provider="licensed-fx-feed",
            source_reference="fx/comparison-123",
            observed_at="2026-08-11T01:00:00Z",
            expires_at="2026-08-11T02:00:00Z",
        ),
        shipping_quote=ShippingQuoteEvidence(
            quote_type="carrier_quote",
            currency="CNY",
            total_amount=90,
            base_amount=90,
            actual_weight_kg=1,
            chargeable_weight_kg=1,
            origin_country="US",
            destination_country="CN",
            service_name="International Priority",
            eta_min_days=6,
            eta_max_days=9,
            provider="licensed-carrier-rate-feed",
            source_reference="shipping/quote-456",
            observed_at="2026-08-11T01:00:00Z",
            expires_at="2026-08-11T02:00:00Z",
        ),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="US",
            destination_country="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0.10,
            import_vat_rate=0.13,
            insurance_cny=10,
            valuation={
                "valuation_method": "transaction_value_cif",
                "goods_value_original": 100,
                "goods_currency": "USD",
                "goods_value_cny": 710,
                "international_shipping_cny": 80,
                "insurance_cny": 10,
                "customs_value_cny": 800,
                "customs_conversion": {
                    "source_currency": "USD",
                    "rate_to_cny": 7.10,
                    "declaration_date": "2026-08-11",
                    "assessment_month": "2026-08",
                    "provider": "licensed-customs-fx-feed",
                    "source_reference": "customs/monthly-rate/2026-08",
                },
                "provider": "licensed-customs-valuation-feed",
                "source_reference": "valuation/cif-123",
            },
            provider="licensed-customs-feed",
            source_reference="CN tariff snapshot 2026",
            effective_date="2026-08-01",
        ),
        source="live",
    )

    prices = await price_compare(
        [candidate],
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )
    shipping = await shipping_calc(
        prices.ranked,
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )

    item = shipping.items[0]
    assert item.price_cny == 720
    assert item.tax_breakdown.customs_value_cny == 800
    assert item.tax_breakdown.customs_valuation.customs_conversion.rate_to_cny == 7.10
    assert item.duty_cny == 80
    assert item.import_vat_cny == pytest.approx(114.4)
    assert item.import_tax_cny == pytest.approx(194.4)


@pytest.mark.asyncio
async def test_transaction_import_without_customs_valuation_is_excluded() -> None:
    candidate = Candidate(
        item_id="missing-customs-valuation",
        platform="amazon",
        title="Offer without customs valuation",
        price=1000,
        currency="CNY",
        shipping_quote=ShippingQuoteEvidence(
            quote_type="carrier_quote",
            currency="CNY",
            total_amount=90,
            base_amount=90,
            actual_weight_kg=1,
            chargeable_weight_kg=1,
            origin_country="US",
            destination_country="CN",
            service_name="International Priority",
            eta_min_days=6,
            eta_max_days=9,
            provider="licensed-carrier-rate-feed",
            source_reference="shipping/quote-789",
            observed_at="2026-08-11T01:00:00Z",
            expires_at="2026-08-11T02:00:00Z",
        ),
        customs=CustomsTaxEvidence(
            hs_code="8518300000",
            country_of_origin="US",
            destination_country="CN",
            import_regime="general_trade",
            rate_type="mfn",
            tariff_rate=0.10,
            import_vat_rate=0.13,
            provider="licensed-customs-feed",
            source_reference="CN tariff snapshot 2026",
            effective_date="2026-08-01",
        ),
        source="live",
    )

    shipping = await shipping_calc(
        (await price_compare([candidate])).ranked,
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )

    assert shipping.items == []
    assert [item.reason_code for item in shipping.tax_exclusions] == ["missing_customs_valuation"]


@pytest.mark.asyncio
async def test_tax_exclusions_reach_terminal_result_and_customer_explanation(tmp_path) -> None:
    exclusion = TaxCalculationExclusion(
        item_id="missing-tax-evidence",
        platform="aliexpress",
        title="Unclassified item",
        reason_code="missing_customs_evidence",
        reason="缺少 HS Code、原产地、进口模式和税率证据。",
    )
    with thread_scope("thread-tax-exclusion", tmp_path):
        result = await shopping_summary(
            ItemPickerOutput(recommendations=[], rejected_count=0),
            [],
            tax_exclusions=[exclusion],
        )

    assert result.tax_exclusions == [exclusion]
    assert "税务证据" in result.final_answer
    assert "1 个候选" in result.calculation_notice


@pytest.mark.asyncio
async def test_shipping_exclusions_reach_terminal_result_and_customer_explanation(tmp_path) -> None:
    exclusion = ShippingCalculationExclusion(
        item_id="missing-shipping-quote",
        platform="amazon",
        title="Offer without a shipping quote",
        reason_code="missing_shipping_quote",
        reason="数据通道未提供面向中国大陆的运费报价。",
    )
    with thread_scope("thread-shipping-exclusion", tmp_path):
        result = await shopping_summary(
            ItemPickerOutput(recommendations=[], rejected_count=0),
            [],
            shipping_exclusions=[exclusion],
        )

    assert result.shipping_exclusions == [exclusion]
    assert "运费报价" in result.final_answer
    assert "1 个候选" in result.calculation_notice


@pytest.mark.asyncio
async def test_price_compare_reports_or_rejects_missing_exchange_rates() -> None:
    usd = Candidate(
        item_id="usd",
        platform="amazon",
        title="USD item",
        price=10,
        currency="USD",
        price_conversion=_live_fx_quote(),
        source="live",
    )
    hkd = Candidate(
        item_id="hkd",
        platform="ebay",
        title="HKD item",
        price=100,
        currency="HKD",
        source="live",
    )

    partial = await price_compare([usd, hkd])
    assert [item.item_id for item in partial.ranked] == ["usd"]
    assert partial.excluded_currencies == ["HKD"]
    assert [(item.item_id, item.reason_code) for item in partial.calculation_exclusions] == [
        ("hkd", "missing_fx_evidence")
    ]

    with pytest.raises(MissingExchangeRatesError) as error:
        await price_compare([hkd])
    assert error.value.currencies == ("HKD",)


@pytest.mark.asyncio
async def test_live_non_cny_offer_without_fx_evidence_cannot_enter_ranking() -> None:
    candidate = Candidate(
        item_id="usd-without-fx-evidence",
        platform="amazon",
        title="USD item without a provider quote",
        price=10,
        currency="USD",
        source="live",
    )

    with pytest.raises(MissingExchangeRatesError) as error:
        await price_compare([candidate])

    assert error.value.currencies == ("USD",)


@pytest.mark.asyncio
async def test_price_compare_uses_and_preserves_offer_fx_quote() -> None:
    quote = CurrencyConversionEvidence(
        source_currency="usd",
        rate_to_cny=7.2345,
        rate_type="provider_quote",
        markup_status="unknown",
        provider="licensed-fx-feed",
        source_reference="quote/fx-123",
        observed_at="2026-08-11T09:30:00+08:00",
        expires_at="2026-08-11T10:00:00+08:00",
    )
    candidate = Candidate(
        item_id="usd-with-provider-quote",
        platform="amazon",
        title="USD item with a provider quote",
        price=10,
        currency="USD",
        price_conversion=quote,
        source="live",
    )

    result = await price_compare(
        [candidate],
        calculated_at=datetime(2026, 8, 11, 1, 45, tzinfo=timezone.utc),
    )

    assert result.ranked[0].price_cny == 72.35
    assert result.ranked[0].price_conversion == quote
    assert result.exchange_rate.source == "offer-level-quotes"
    assert result.exchange_rate.effective_date == "2026-08-11T01:30:00Z"
    assert result.exchange_rate.providers == ["licensed-fx-feed"]
    assert result.exchange_rate.quote_count == 1
    assert "最终支付" in result.exchange_rate.settlement_notice


@pytest.mark.asyncio
async def test_native_cny_offer_does_not_report_an_unused_foreign_exchange_quote() -> None:
    candidate = Candidate(
        item_id="native-cny-with-extra-fx",
        platform="shopee",
        title="Native CNY offer with an irrelevant provider field",
        price=99.995,
        currency="CNY",
        price_conversion=_live_fx_quote(),
        source="live",
    )

    result = await price_compare([candidate])

    assert result.ranked[0].price_cny == 100.0
    assert result.ranked[0].price_conversion is None
    assert result.exchange_rate.source == "native-CNY"
    assert result.exchange_rate.providers == []
    assert result.exchange_rate.quote_count == 0


@pytest.mark.asyncio
async def test_price_compare_excludes_expired_offer_fx_quote() -> None:
    expired = Candidate(
        item_id="expired-fx",
        platform="amazon",
        title="Offer with an expired FX quote",
        price=10,
        currency="USD",
        price_conversion=CurrencyConversionEvidence(
            source_currency="USD",
            rate_to_cny=7.2,
            rate_type="provider_quote",
            provider="licensed-fx-feed",
            source_reference="quote/expired",
            observed_at="2000-01-01T00:00:00Z",
            expires_at="2000-01-01T01:00:00Z",
        ),
        source="live",
    )
    native_cny = Candidate(
        item_id="native-cny",
        platform="shopee",
        title="Native CNY offer",
        price=80,
        currency="CNY",
        source="live",
    )

    result = await price_compare(
        [expired, native_cny],
        calculated_at=datetime(2000, 1, 1, 2, tzinfo=timezone.utc),
    )

    assert [item.item_id for item in result.ranked] == ["native-cny"]
    assert [(item.item_id, item.reason_code) for item in result.calculation_exclusions] == [
        ("expired-fx", "invalid_fx_evidence")
    ]
    assert "已过期" in result.calculation_exclusions[0].reason


@pytest.mark.asyncio
async def test_live_fx_quote_without_validity_window_is_excluded() -> None:
    undated = Candidate(
        item_id="fx-without-expiry",
        platform="amazon",
        title="Offer with no FX validity window",
        price=10,
        currency="USD",
        price_conversion=CurrencyConversionEvidence(
            source_currency="USD",
            rate_to_cny=7.2,
            rate_type="provider_quote",
            provider="licensed-fx-feed",
            source_reference="quote/no-expiry",
            observed_at="2026-08-11T01:00:00Z",
        ),
        source="live",
    )
    native_cny = Candidate(
        item_id="native-cny",
        platform="shopee",
        title="Native CNY offer",
        price=80,
        currency="CNY",
        source="live",
    )

    result = await price_compare(
        [undated, native_cny],
        calculated_at=datetime(2026, 8, 11, 1, 30, tzinfo=timezone.utc),
    )

    assert [item.item_id for item in result.ranked] == ["native-cny"]
    assert result.calculation_exclusions[0].reason_code == "invalid_fx_evidence"
    assert "有效期" in result.calculation_exclusions[0].reason


@pytest.mark.asyncio
async def test_offer_fx_evidence_cannot_be_overridden_by_legacy_environment_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "true")
    monkeypatch.setenv("FX_RATES_JSON", '{"USD": 999}')
    offer = (await item_search("耳机", "amazon", top_k=1)).candidates[0]

    result = await price_compare([offer])

    assert offer.price_conversion is not None
    assert result.ranked[0].price_cny == pytest.approx(
        round(offer.price * offer.price_conversion.rate_to_cny, 2)
    )
    assert result.exchange_rate.source == "offer-level-quotes"
    assert result.exchange_rate.providers == ["deterministic-sandbox-fx-fixture"]


@pytest.mark.asyncio
async def test_price_compare_keeps_cheapest_per_platform_outside_top_n() -> None:
    candidates = [
        Candidate(
            item_id="amazon-expensive",
            platform="amazon",
            title="Amazon expensive",
            price=100,
            currency="CNY",
            source="live",
        ),
        Candidate(
            item_id="shopee-cheap",
            platform="shopee",
            title="Shopee cheap",
            price=1,
            currency="CNY",
            source="live",
        ),
    ]

    result = await price_compare(candidates, top_n=1)

    assert [item.item_id for item in result.ranked] == ["shopee-cheap"]
    assert set(result.cheapest_per_platform) == {"amazon", "shopee"}
    assert result.cheapest_per_platform["amazon"].item_id == "amazon-expensive"


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), float("-inf"), 0, -1])
def test_currency_conversion_rejects_non_positive_or_non_finite_rates(rate: float) -> None:
    with pytest.raises(ValueError):
        CurrencyConversionEvidence(
            source_currency="USD",
            rate_to_cny=rate,
            rate_type="provider_quote",
            provider="licensed-fx-feed",
            source_reference="quote/invalid-rate",
            observed_at="2026-08-11T01:00:00Z",
            expires_at="2026-08-11T02:00:00Z",
        )


def test_currency_conversion_requires_timezone_aware_observation() -> None:
    with pytest.raises(ValueError, match="timezone"):
        CurrencyConversionEvidence(
            source_currency="USD",
            rate_to_cny=7.2,
            rate_type="provider_quote",
            provider="licensed-fx-feed",
            source_reference="quote/no-timezone",
            observed_at="2026-08-11T01:00:00",
            expires_at="2026-08-11T02:00:00Z",
        )


@pytest.mark.asyncio
async def test_price_compare_excludes_invalid_amounts_without_ranking_them() -> None:
    invalid_negative = Candidate.model_construct(
        item_id="negative",
        platform="amazon",
        title="Negative amount",
        price=-1,
        currency="CNY",
        source="live",
    )
    invalid_nan = Candidate.model_construct(
        item_id="nan",
        platform="amazon",
        title="NaN amount",
        price=float("nan"),
        currency="CNY",
        source="live",
    )
    valid = Candidate(
        item_id="valid",
        platform="amazon",
        title="Valid amount",
        price=10,
        currency="CNY",
        source="live",
    )

    result = await price_compare([invalid_negative, invalid_nan, valid])

    assert [item.item_id for item in result.ranked] == ["valid"]
    assert [(item.item_id, item.reason_code) for item in result.calculation_exclusions] == [
        ("negative", "invalid_amount"),
        ("nan", "invalid_amount"),
    ]


@pytest.mark.asyncio
async def test_local_category_insight_is_structured() -> None:
    result = await category_insight("耳机", "quick")
    assert result.provider.source == "curated"
    assert result.components
    assert {tier.tier for tier in result.price_tiers} == {"budget", "mid", "premium"}


def test_preference_records_are_merged_without_overwriting_lists() -> None:
    remembered = {"avoid": ["皮革"], "soft_preferences": ["轻便"]}
    extracted = {"avoid": ["塑料"], "style_preferences": ["简约"]}
    merged = merge_preference_records(remembered, extracted)
    assert merged["avoid"] == ["皮革", "塑料"]
    assert merged["soft_preferences"] == ["轻便"]
    assert merged["style_preferences"] == ["简约"]
