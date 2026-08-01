from __future__ import annotations

from typing import ClassVar

import httpx
import pytest

from app.tools.item_search import item_search
from app.tools.marketplace_gateway import normalize_gateway_response


class _GatewayResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "provider": "licensed-amazon-feed",
            "items": [
                {
                    "offer_id": "amazon-offer-1",
                    "title": "Gateway headphones",
                    "price": 99,
                    "currency": "USD",
                    "retrieved_at": "2026-07-30T10:00:00Z",
                    "product_url": "https://shop.example/offers/amazon-offer-1",
                }
            ],
        }


class _GatewayClient:
    request: ClassVar[dict[str, object]] = {}

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(
        self,
        endpoint: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
    ) -> _GatewayResponse:
        self.request = {"endpoint": endpoint, "params": params, "headers": headers}
        type(self).request = self.request
        return _GatewayResponse()


def test_gateway_normalizes_wrapped_aliases_into_product_evidence() -> None:
    candidates = normalize_gateway_response(
        {
            "provider": "licensed-ebay-feed",
            "data": {
                "products": [
                    {
                        "id": "offer-42",
                        "name": "Acme X1 256 GB",
                        "sale_price": "129.99",
                        "currency_code": "usd",
                        "source": "ebay-buy-browse",
                        "stock_status": "IN_STOCK",
                        "observed_at": "2020-01-02T03:04:05+08:00",
                        "url": "https://shop.example/items/offer-42",
                        "identity": {
                            "ean": "4006381333931",
                            "brand": "Acme",
                            "model": "X1",
                            "variant": {"capacity": "256 GB", "condition": "new"},
                        },
                    }
                ]
            },
        },
        "ebay",
    )

    assert len(candidates) == 1
    offer = candidates[0]
    assert (offer.marketplace, offer.platform) == ("ebay", "ebay")
    assert (offer.offer_id, offer.item_id) == ("offer-42", "offer-42")
    assert offer.identity.model_dump() == {
        "gtin": "4006381333931",
        "mpn": None,
        "brand": "Acme",
        "model": "X1",
    }
    assert offer.variant_attributes == {"capacity": "256 GB", "condition": "new"}
    assert offer.availability == "in_stock"
    assert offer.retrieved_at == "2020-01-01T19:04:05Z"
    assert (offer.price, offer.currency) == (129.99, "USD")
    assert offer.provenance.model_dump() == {
        "kind": "marketplace_gateway",
        "provider": "licensed-ebay-feed",
        "upstream_source": "ebay-buy-browse",
    }
    assert offer.link_kind == "product_detail"
    assert offer.product_url == "https://shop.example/items/offer-42"


@pytest.mark.parametrize("wrapper", ["items", "products"])
def test_gateway_accepts_legacy_nested_collection_wrappers(wrapper: str) -> None:
    candidates = normalize_gateway_response(
        {
            "provider": "legacy-feed",
            wrapper: {
                wrapper: [
                    {
                        "id": "legacy-offer",
                        "name": "Legacy wrapper item",
                        "price": 10,
                        "currency": "USD",
                    }
                ]
            },
        },
        "amazon",
    )

    assert len(candidates) == 1
    assert candidates[0].offer_id == "legacy-offer"
    assert candidates[0].provenance is not None
    assert candidates[0].provenance.provider == "legacy-feed"


def test_gateway_keeps_unknown_evidence_null_and_types_search_links() -> None:
    candidates = normalize_gateway_response(
        {
            "offers": [
                {
                    "title": "Provider result without optional evidence",
                    "price": 20,
                    "currency": "USD",
                    "rating": "not-a-number",
                    "sales": "unknown",
                    "image_url": "data:image/png;base64,unsafe",
                    "product_url": "javascript:alert(1)",
                    "search_url": "https://shop.example/search?q=provider+result",
                    "link_kind": "marketplace_search",
                    "retrieved_at": "not-a-timestamp",
                    "stock_status": "provider-mystery-state",
                }
            ]
        },
        "amazon",
    )

    assert len(candidates) == 1
    offer = candidates[0]
    assert offer.item_id.startswith("candidate-")
    assert offer.offer_id is None
    assert offer.identity.model_dump() == {
        "gtin": None,
        "mpn": None,
        "brand": None,
        "model": None,
    }
    assert offer.variant_attributes == {}
    assert offer.availability is None
    assert offer.retrieved_at is None
    assert offer.rating is None
    assert offer.sales is None
    assert offer.image_url is None
    assert offer.product_url == "https://shop.example/search?q=provider+result"
    assert offer.link_kind == "marketplace_search"


def test_gateway_discards_invalid_optional_numbers_without_aborting_offer() -> None:
    candidates = normalize_gateway_response(
        [
            {
                "title": "Non-finite variant",
                "price": 20,
                "currency": "USD",
                "variant_attributes": {"weight": float("nan"), "color": "black"},
            },
            {
                "title": "Non-finite legacy attribute",
                "price": 21,
                "currency": "USD",
                "attributes": {"weight": float("inf"), "material": "cotton"},
            },
        ],
        "amazon",
    )

    assert [offer.variant_attributes for offer in candidates] == [
        {"color": "black"},
        {"material": "cotton"},
    ]


def test_gateway_does_not_invent_missing_provenance_or_accept_malformed_ports() -> None:
    candidates = normalize_gateway_response(
        [
            {
                "title": "Unknown source",
                "price": 20,
                "currency": "USD",
                "product_url": "https://shop.example:bad/item",
            },
            {
                "title": "Unknown source with upstream label",
                "price": 21,
                "currency": "USD",
                "product_url": "https://shop.example:99999/item",
                "source": "unverified-provider",
            },
        ],
        "amazon",
    )

    assert candidates[0].product_url is None
    assert candidates[0].provenance is None
    assert candidates[1].product_url is None
    assert candidates[1].provenance is not None
    assert candidates[1].provenance.provider is None
    assert candidates[1].provenance.upstream_source == "unverified-provider"


@pytest.mark.parametrize("price", [True, False, "NaN", "Infinity", "-Infinity", -1, "invalid"])
def test_gateway_rejects_invalid_required_prices(price: object) -> None:
    candidates = normalize_gateway_response(
        [{"offer_id": "bad-price", "title": "Bad price", "price": price, "currency": "USD"}],
        "amazon",
    )

    assert candidates == []


def test_gateway_rejects_offers_claiming_a_different_marketplace() -> None:
    candidates = normalize_gateway_response(
        {
            "results": [
                {
                    "offer_id": "wrong-marketplace",
                    "marketplace": "amazon",
                    "title": "Wrong branch",
                    "price": 10,
                    "currency": "USD",
                },
                {
                    "offer_id": "right-marketplace",
                    "platform": "EBAY",
                    "title": "Right branch",
                    "price": 12,
                    "currency": "USD",
                },
            ]
        },
        "ebay",
    )

    assert [candidate.offer_id for candidate in candidates] == ["right-marketplace"]


def test_gateway_preserves_metadata_from_a_nested_data_wrapper() -> None:
    candidates = normalize_gateway_response(
        {
            "data": {
                "provider": "gateway-v2",
                "observed_at": "2026-07-29T12:00:00Z",
                "provenance": {"source": "licensed-provider-catalog"},
                "items": [
                    {
                        "sku": "nested-1",
                        "product_name": "Nested wrapper item",
                        "current_price": 42,
                        "currency_code": "EUR",
                    }
                ],
            }
        },
        "shopee",
    )

    offer = candidates[0]
    assert offer.retrieved_at == "2026-07-29T12:00:00Z"
    assert offer.provenance.provider == "gateway-v2"
    assert offer.provenance.upstream_source == "licensed-provider-catalog"


async def test_sandbox_uses_the_offer_contract_without_claiming_a_detail_link(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "true")

    result = await item_search("预算 1200 元找轻便耳机", "amazon", top_k=1)

    offer = result.candidates[0]
    assert (offer.marketplace, offer.platform) == ("amazon", "amazon")
    assert offer.item_id.startswith("fixture-amazon-")
    assert offer.offer_id is None
    assert offer.identity.model_dump() == {
        "gtin": None,
        "mpn": None,
        "brand": None,
        "model": None,
    }
    assert offer.variant_attributes == {
        "weight_kg": 0.34,
        "material": "织物",
        "style": "简约",
    }
    assert offer.availability is None
    assert offer.retrieved_at is None
    assert offer.provenance.model_dump() == {
        "kind": "sandbox_fixture",
        "provider": "amazon-sandbox",
        "upstream_source": "deterministic-fixture-catalog",
    }
    assert offer.product_url.startswith("https://www.amazon.com/s?")
    assert offer.link_kind == "marketplace_search"


async def test_live_search_preserves_the_normalized_gateway_contract(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "false")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example/amazon/search")
    monkeypatch.setenv("AMAZON_API_KEY", "gateway-secret")
    monkeypatch.setattr(httpx, "AsyncClient", _GatewayClient)

    result = await item_search("headphones", "amazon", top_k=1)

    assert _GatewayClient.request == {
        "endpoint": "https://gateway.example/amazon/search",
        "params": {"query": "headphones", "top_k": 1},
        "headers": {
            "Authorization": "Bearer gateway-secret",
            "X-API-Key": "gateway-secret",
        },
    }
    assert result.provider.provider == "licensed-amazon-feed"
    offer = result.candidates[0]
    assert offer.offer_id == "amazon-offer-1"
    assert offer.retrieved_at == "2026-07-30T10:00:00Z"
    assert offer.provenance.provider == "licensed-amazon-feed"
    assert offer.link_kind == "product_detail"
