from __future__ import annotations

import pytest

from app.memory.injector import merge_preference_records
from app.schemas import Candidate
from app.tools.category_insight import category_insight
from app.tools.item_picker import item_picker
from app.tools.item_search import _parse_live_item, item_search
from app.tools.planner import planner
from app.tools.price_compare import MissingExchangeRatesError, price_compare
from app.tools.shipping_calc import shipping_calc


@pytest.mark.asyncio
async def test_planner_extracts_budget_category_and_constraints() -> None:
    plan = await planner("预算 1200 元，找轻便降噪耳机，不要皮革，寄到上海")
    assert plan.budget_cny == 1200
    assert plan.category == "耳机"
    assert "轻便" in plan.soft_preferences
    assert "不含皮革" in plan.material_preferences
    assert plan.destination == "上海"


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
        source="live",
    )
    prices = await price_compare([candidate])
    assert prices.ranked[0].price_cny == 718
    shipping = await shipping_calc(prices.ranked)
    item = shipping.items[0]
    assert item.shipping_cny == 85
    assert item.duty_cny == pytest.approx(93.34)
    assert item.image_url == candidate.image_url
    plan = await planner("预算 1000 元，找轻便耳机，不要皮革")
    picks = await item_picker(shipping, plan)
    assert picks.recommendations[0].item_id == "one"
    assert picks.recommendations[0].rank == 1


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
