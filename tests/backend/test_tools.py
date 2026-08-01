from __future__ import annotations

from urllib.parse import unquote_plus

import pytest

from app.memory.injector import merge_preference_records
from app.schemas import Candidate, OfferProvenance, ProductIdentity
from app.tools.category_insight import category_insight
from app.tools.item_picker import item_picker
from app.tools.item_search import _parse_live_item, item_search
from app.tools.planner import planner
from app.tools.price_compare import MissingExchangeRatesError, price_compare
from app.tools.query_parser import extract_product_subject
from app.tools.shipping_calc import shipping_calc


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
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "http://127.0.0.1:1/search")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")
    monkeypatch.setenv("PROVIDER_TIMEOUT_SECONDS", "1")

    result = await item_search("降噪耳机", "amazon", top_k=2)

    assert result.candidates == []
    assert result.provider.source == "live"
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
        rating=4.8,
        sales=200,
        image_url="https://example.com/image.jpg",
        product_url="https://example.com/item",
        attributes={"weight_kg": 0.34, "material": "织物"},
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
        source="live",
    )
    prices = await price_compare([candidate])
    assert prices.ranked[0].price_cny == 718
    assert prices.exchange_rate.source == "reference-table"
    assert prices.exchange_rate.effective_date != "unspecified"
    assert prices.exchange_rate.calculation_basis == "original_amount * rate_to_cny"
    shipping = await shipping_calc(prices.ranked)
    item = shipping.items[0]
    assert item.shipping_cny == 85
    assert item.duty_cny == pytest.approx(93.34)
    assert item.shipping_estimate.estimated is True
    assert item.shipping_estimate.source == "shipping_rules"
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
async def test_real_item_without_weight_discloses_shipping_estimate() -> None:
    candidate = Candidate(
        item_id="unknown-weight",
        platform="ebay",
        title="Provider item",
        price=50,
        currency="USD",
        attributes={},
        source="live",
    )
    prices = await price_compare([candidate])
    shipping = await shipping_calc(prices.ranked)
    assert shipping.items[0].note == "重量缺失，运费按0.5kg估算"


@pytest.mark.asyncio
async def test_price_compare_reports_or_rejects_missing_exchange_rates() -> None:
    usd = Candidate(
        item_id="usd",
        platform="amazon",
        title="USD item",
        price=10,
        currency="USD",
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
        ("hkd", "unsupported_currency")
    ]

    with pytest.raises(MissingExchangeRatesError) as error:
        await price_compare([hkd])
    assert error.value.currencies == ("HKD",)


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", ["NaN", "Infinity", "-Infinity"])
async def test_price_compare_rejects_non_finite_exchange_rates(monkeypatch, rate: str) -> None:
    monkeypatch.setenv("FX_RATES_JSON", f'{{"USD": {rate}}}')
    candidate = Candidate(
        item_id="usd",
        platform="amazon",
        title="USD item",
        price=10,
        currency="USD",
        source="live",
    )

    with pytest.raises(ValueError, match="positive rates"):
        await price_compare([candidate])


@pytest.mark.asyncio
async def test_price_compare_requires_effective_date_for_custom_exchange_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FX_RATES_JSON", '{"USD": 7.2}')
    monkeypatch.delenv("FX_RATES_AS_OF", raising=False)
    candidate = Candidate(
        item_id="usd",
        platform="amazon",
        title="USD item",
        price=10,
        currency="USD",
        source="live",
    )

    with pytest.raises(ValueError, match="FX_RATES_AS_OF"):
        await price_compare([candidate])


@pytest.mark.asyncio
async def test_price_compare_excludes_invalid_amounts_without_ranking_them() -> None:
    invalid_negative = Candidate.model_construct(
        item_id="negative",
        platform="amazon",
        title="Negative amount",
        price=-1,
        currency="USD",
        source="live",
    )
    invalid_nan = Candidate.model_construct(
        item_id="nan",
        platform="amazon",
        title="NaN amount",
        price=float("nan"),
        currency="USD",
        source="live",
    )
    valid = Candidate(
        item_id="valid",
        platform="amazon",
        title="Valid amount",
        price=10,
        currency="USD",
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
