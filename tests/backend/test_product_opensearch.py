from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from app.config import get_settings
from app.data.ebay_browse import browse_context_header, browse_item_to_record, delivery_filter
from app.data.product_document import candidate_from_product_record, normalize_product_record
from app.schemas import Candidate, CustomsExchangeRateEvidence, ShippingQuoteEvidence
from app.tools import customs_fx as customs_fx_module
from app.tools import customs_provider as customs_provider_module
from app.tools import fx_reference as fx_reference_module
from app.tools import product_search as product_search_module
from app.tools.china_customs import ChinaCustomsRates, _parse_inner_response
from app.tools.customs_provider import CustomsEvidenceError
from app.tools.fx_reference import conversion_from_rates
from app.tools.product_search import ProductSearchFilters, build_product_search_body
from scripts.fetch_ebay_browse_snapshot import _application_token


def test_normalize_product_record_keeps_platform_and_snapshot_metadata() -> None:
    record = normalize_product_record(
        {
            "item_id": "ebay:v1|123",
            "platform": "ebay",
            "title": "Noise cancelling headphones",
            "price": "99.9",
            "currency": "USD",
            "categories": ["Headphones"],
            "category_ids": ["112529"],
            "source": "live",
            "snapshot_at": "2026-08-27T01:00:00+00:00",
            "expires_at": "2026-08-27T07:00:00+00:00",
            "provider": "ebay-buy-browse-snapshot",
        }
    )

    assert record is not None
    assert record["platform"] == "ebay"
    assert record["category_ids"] == ["112529"]
    assert record["snapshot_at"] == "2026-08-27T01:00:00Z"
    assert record["expires_at"] == "2026-08-27T07:00:00Z"
    candidate = candidate_from_product_record(record, 0.81)
    assert candidate.item_id == "ebay:v1|123"
    assert candidate.attributes["opensearch_score"] == 0.81


def test_normalize_product_record_materializes_cny_price_from_conversion() -> None:
    record = normalize_product_record(
        {
            "item_id": "ebay:v1|price",
            "platform": "ebay",
            "title": "Converted headphones",
            "price": 99.99,
            "currency": "USD",
            "price_conversion": {
                "source_currency": "USD",
                "target_currency": "CNY",
                "rate_to_cny": 7.2,
            },
            "source": "live",
        }
    )

    assert record is not None
    assert record["price_cny"] == 719.93


def test_customs_exchange_rate_uses_previous_month_third_wednesday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        customs_fx_module,
        "_fetch_series",
        lambda _type_id, _timeout: {"2026-07-15": 6.791},
    )

    evidence = customs_fx_module.fetch_customs_exchange_rate("USD", declaration_date="2026-08-28")

    assert evidence.rate_to_cny == 6.791
    assert evidence.declaration_date == "2026-08-28"
    assert evidence.assessment_month == "2026-08"
    assert "date=2026-07-15" in evidence.source_reference


def test_product_search_body_uses_bm25_and_knn_with_efficient_filter() -> None:
    filters = ProductSearchFilters(
        platform="ebay",
        marketplace_id="EBAY_US",
        price_max_cny=1200,
        category_ids=("112529",),
        conditions=("NEW",),
        sources=("live",),
        require_fresh=True,
    )
    body = build_product_search_body(
        "降噪耳机",
        vector=[0.1, 0.2],
        filters=filters,
        size=10,
        vector_k=40,
    )
    hybrid = body["query"]["hybrid"]["queries"]
    bm25 = hybrid[0]["bool"]
    knn = hybrid[1]["knn"]["embedding"]
    assert bm25["must"]["multi_match"]["query"] == "降噪耳机"
    assert bm25["filter"] == filters.to_query()
    assert knn["k"] == 40
    assert knn["filter"] == filters.to_query()
    assert {"term": {"platform": "ebay"}} in knn["filter"]["bool"]["filter"]
    assert {"terms": {"source": ["live"]}} in knn["filter"]["bool"]["filter"]
    assert {"range": {"expires_at": {"gte": "now"}}} in knn["filter"]["bool"]["filter"]


def test_product_search_body_without_vector_keeps_scalar_filter() -> None:
    body = build_product_search_body(
        "charger",
        vector=None,
        filters=ProductSearchFilters(platform="amazon"),
        size=5,
    )
    query = body["query"]["bool"]
    assert query["filter"] == ProductSearchFilters(platform="amazon").to_query()
    assert query["must"]["multi_match"]["query"] == "charger"


def test_ebay_record_projects_browse_item_to_snapshot_record() -> None:
    record = browse_item_to_record(
        {
            "itemId": "v1|1|0|abc",
            "title": "Sony headphones",
            "price": {"value": "129.99", "currency": "USD"},
            "itemWebUrl": "https://www.ebay.com/itm/abc",
            "image": {"imageUrl": "https://i.ebayimg.com/image.jpg"},
            "categories": [{"categoryId": "112529", "categoryName": "Headphones"}],
            "localizedAspects": [{"name": "Brand", "value": "Sony"}],
            "condition": "New",
            "buyingOptions": ["FIXED_PRICE"],
        },
        marketplace_id="EBAY_US",
        snapshot_at="2026-08-27T01:00:00Z",
        expires_at="2026-08-27T07:00:00Z",
    )
    assert record is not None
    assert record["item_id"] == "ebay:v1|1|0|abc"
    assert record["brand"] == "Sony"
    assert record["category_ids"] == ["112529"]
    assert record["marketplace_id"] == "EBAY_US"


def test_ebay_browse_projection_keeps_china_shipping_evidence() -> None:
    record = browse_item_to_record(
        {
            "itemId": "v1|1|0|shipping",
            "title": "Wireless headphones",
            "price": {
                "value": "99.00",
                "currency": "CNY",
                "convertedFromValue": "14.00",
                "convertedFromCurrency": "USD",
            },
            "itemWebUrl": "https://www.ebay.com/itm/shipping",
            "categories": [{"categoryId": "112529", "categoryName": "Headphones"}],
            "itemLocation": {"country": "US"},
            "shippingOptions": [
                {
                    "shippingCostType": "CALCULATED",
                    "shippingCost": {"value": "25.00", "currency": "CNY"},
                    "minEstimatedDeliveryDate": "2026-09-01T00:00:00Z",
                    "maxEstimatedDeliveryDate": "2026-09-05T00:00:00Z",
                }
            ],
            "localizedAspects": [
                {"name": "HS Code", "value": "8518300000"},
                {"name": "Country of Origin", "value": "US"},
            ],
        },
        marketplace_id="EBAY_US",
        snapshot_at="2026-08-28T00:00:00Z",
        expires_at="2026-08-28T06:00:00Z",
        destination_country="CN",
        destination_postal_code="100000",
    )

    assert record is not None
    assert record["shipping_quote"]["currency"] == "CNY"
    assert record["shipping_quote"]["origin_country"] == "US"
    assert record["shipping_quote"]["destination_country"] == "CN"
    assert record["shipping_quote"]["quote_type"] == "marketplace_estimate"
    assert record["hs_code"] == "8518300000"
    assert record["country_of_origin"] == "US"
    projected = candidate_from_product_record(normalize_product_record(record) or {})
    assert projected.shipping_quote is not None
    assert projected.shipping_quote.total_amount == 25
    assert projected.attributes["hs_code"] == "8518300000"


def test_ebay_context_and_delivery_filter_are_encoded_for_cn() -> None:
    assert browse_context_header("cn", "100000") == "contextualLocation=country%3DCN%2Czip%3D100000"
    assert delivery_filter("cn", "100000") == "deliveryCountry:CN,deliveryPostalCode:100000"


def test_ecb_conversion_uses_euro_cross_rate_and_expires_from_fetch_time() -> None:
    conversion = conversion_from_rates(
        "USD",
        "2026-08-28",
        {"EUR": 1.0, "USD": 1.25, "CNY": 8.0},
        now=datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc),
    )
    assert conversion is not None
    assert conversion.rate_to_cny == 6.4
    assert conversion.provider == "ecb-reference-rates"
    assert conversion.expires_at == "2026-08-29T02:00:00Z"


@pytest.mark.asyncio
async def test_enrich_price_conversions_fills_snapshot_offers_with_current_reference_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = Candidate(
        item_id="amazon:historical-usd",
        platform="amazon",
        title="Historical catalog offer",
        price=10,
        currency="USD",
        source="fixture",
    )
    quote = conversion_from_rates(
        "USD",
        "2026-08-28",
        {"EUR": 1.0, "USD": 1.25, "CNY": 8.0},
        now=datetime(2026, 8, 28, 2, tzinfo=timezone.utc),
    )
    assert quote is not None

    async def fake_fetch(currencies: set[str], *, timeout: float = 20):
        assert currencies == {"USD"}
        assert timeout == 20
        return {"USD": quote}

    monkeypatch.setattr(fx_reference_module, "fetch_conversions", fake_fetch)
    enriched = await fx_reference_module.enrich_price_conversions([candidate])

    assert enriched[0].price_conversion == quote


def test_customs_response_parser_handles_official_json_like_payload() -> None:
    rows = _parse_inner_response(
        {
            "res": '{statue:1,message:"查询成功",totalCount:1,data:[{"codeTs":"8518300000","gName":"耳机","impDiscountRate":"0","impOrdinaryRate":"40%"}]}'
        }
    )
    assert rows == [
        {
            "codeTs": "8518300000",
            "gName": "耳机",
            "impDiscountRate": "0",
            "impOrdinaryRate": "40%",
        }
    ]


def test_customs_auto_baseline_requires_explicit_classification(monkeypatch) -> None:
    candidate = candidate_from_product_record(
        {
            "item_id": "ebay:v1|missing-classification",
            "platform": "ebay",
            "title": "Headphones",
            "price": 99,
            "currency": "CNY",
            "source": "live",
            "provider": "ebay",
            "attributes": {},
        }
    )
    monkeypatch.setenv("CUSTOMS_IMPORT_REGIME", "general_trade")
    with pytest.raises(CustomsEvidenceError, match="explicit hs_code"):
        from app.tools.customs_provider import _input_from_candidate

        _input_from_candidate(candidate)


@pytest.mark.asyncio
async def test_customs_enrichment_uses_independent_monthly_fx_for_foreign_goods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = Candidate(
        item_id="ebay:v1|classified",
        platform="ebay",
        title="Test headphones",
        price=99.99,
        currency="USD",
        source="live",
        attributes={"hs_code": "8518300000", "country_of_origin": "US"},
        shipping_quote=ShippingQuoteEvidence(
            quote_type="marketplace_estimate",
            currency="CNY",
            total_amount=25,
            origin_country="US",
            destination_country="CN",
            service_name="standard",
            eta_min_days=5,
            eta_max_days=8,
            provider="shipping-provider",
            source_reference="shipping-1",
            observed_at="2026-08-28T00:00:00Z",
            expires_at="2026-08-28T06:00:00Z",
        ),
    )

    async def fake_lookup(*args, **kwargs) -> ChinaCustomsRates:
        del args, kwargs
        return ChinaCustomsRates(
            hs_code="8518300000",
            goods_name="耳机",
            mfn_rate=0.1,
            ordinary_rate=0.4,
            temporary_rate=None,
            consumption_tax_rate=0,
            import_vat_rate=0.13,
            agreement_rate=None,
            agreement_name=None,
            effective_date="2026-08-28",
            source_reference="customs-rate-1",
        )

    async def fake_customs_fx(*args, **kwargs) -> CustomsExchangeRateEvidence:
        del args, kwargs
        return CustomsExchangeRateEvidence(
            source_currency="USD",
            rate_to_cny=6.8,
            declaration_date="2026-08-28",
            assessment_month="2026-08",
            provider="pboc-benchmark-rate",
            source_reference="pboc-rate-1",
        )

    monkeypatch.setenv("CUSTOMS_IMPORT_REGIME", "general_trade")
    monkeypatch.setenv("CUSTOMS_RATE_TYPE", "mfn")
    monkeypatch.setattr(customs_provider_module, "lookup_rates_async", fake_lookup)
    monkeypatch.setattr(
        customs_provider_module,
        "fetch_customs_exchange_rate_async",
        fake_customs_fx,
    )

    enriched = await customs_provider_module.enrich_general_trade(candidate, shipping_cny=25)

    assert enriched.customs is not None
    assert enriched.customs.valuation is not None
    assert enriched.customs.valuation.goods_value_cny == 679.93
    assert enriched.customs.valuation.customs_conversion is not None
    assert enriched.customs.valuation.customs_conversion.provider == "pboc-benchmark-rate"


def test_snapshot_manifest_coverage_is_resolved_from_repo_root(monkeypatch, tmp_path: Path) -> None:
    import importlib

    module = importlib.import_module("app.tools.item_search")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"queries": ["wireless headphones"]}), encoding="utf-8")
    monkeypatch.setenv("EBAY_SNAPSHOT_MANIFEST", str(manifest))
    assert module._snapshot_manifest_covers_query("wireless headphones", "ebay")
    assert module._snapshot_manifest_covers_query("预算 2000 元推荐无线耳机", "ebay")
    assert not module._snapshot_manifest_covers_query("xylophone for children", "ebay")


def test_ebay_application_token_uses_basic_client_credentials(monkeypatch) -> None:
    monkeypatch.setenv("EBAY_CLIENT_ID", "app-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "client-secret")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"access_token": "token", "expires_in": 7200})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = _application_token(client, "sandbox", "scope-a")

    expected = base64.b64encode(b"app-id:client-secret").decode()
    assert token == "token"
    assert seen["authorization"] == f"Basic {expected}"
    assert seen["body"] == {"grant_type": "client_credentials", "scope": "scope-a"}


@pytest.mark.asyncio
async def test_search_products_sends_hybrid_request_with_common_filter(monkeypatch) -> None:
    monkeypatch.setenv("OPENSEARCH_URL", "http://opensearch.test")
    monkeypatch.setenv("OPENSEARCH_PRODUCT_INDEX", "shopping_agent_products")
    monkeypatch.setenv("TOWER_QUERY_ENDPOINT", "http://tower.test/query")
    monkeypatch.setenv("PRODUCT_SEARCH_EMBEDDING_DIMENSION", "2")

    class FakeClient:
        requests: ClassVar[list[dict[str, object]]] = []

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            del args

        async def post(self, url, *, params, json):
            self.requests.append({"url": url, "params": params, "json": json})
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "total": {"value": 1},
                        "hits": [
                            {
                                "_score": 0.8,
                                "_source": {
                                    "item_id": "ebay:v1|1",
                                    "platform": "ebay",
                                    "title": "Test headphones",
                                    "price": 20,
                                    "currency": "USD",
                                    "source": "live",
                                    "provider": "ebay-snapshot",
                                },
                            }
                        ],
                    }
                },
                request=httpx.Request("POST", url),
            )

    async def fake_encode_query(_query: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr(product_search_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(product_search_module, "encode_query", fake_encode_query)
    settings = get_settings()

    result = await product_search_module.search_products(
        "耳机",
        "ebay",
        top_k=5,
        settings=settings,
    )

    assert len(result.candidates) == 1
    assert result.vector_used
    request = FakeClient.requests[0]
    body = request["json"]
    queries = body["query"]["hybrid"]["queries"]
    assert queries[0]["bool"]["filter"]["bool"]["filter"][0] == {"term": {"platform": "ebay"}}
    assert queries[1]["knn"]["embedding"]["filter"]["bool"]["filter"][0] == {
        "term": {"platform": "ebay"}
    }
    assert request["params"] == {"search_pipeline": "shopping-agent-product-hybrid-pipeline"}
