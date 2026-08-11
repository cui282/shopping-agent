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


class _ProviderOmittingResponse(_GatewayResponse):
    def json(self) -> dict[str, object]:
        return {
            "items": [
                {
                    "offer_id": "amazon-offer-without-provider",
                    "title": "Provider channel headphones",
                    "price": 89,
                    "currency": "USD",
                    "product_url": "https://shop.example/offers/amazon-provider-channel",
                }
            ]
        }


class _ProviderOmittingClient(_GatewayClient):
    async def get(
        self,
        endpoint: str,
        *,
        params: dict[str, object],
        headers: dict[str, str],
    ) -> _ProviderOmittingResponse:
        self.request = {"endpoint": endpoint, "params": params, "headers": headers}
        type(self).request = self.request
        return _ProviderOmittingResponse()


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


def test_gateway_maps_provider_customs_classification_and_rate_snapshot() -> None:
    candidates = normalize_gateway_response(
        {
            "provider": "licensed-shopping-data-provider",
            "items": [
                {
                    "offer_id": "taxed-offer",
                    "title": "Imported fragrance",
                    "price": 1000,
                    "currency": "CNY",
                    "customs": {
                        "hs_code": "3303000010",
                        "origin_country": "fr",
                        "destination_country": "cn",
                        "ship_from_country": "sg",
                        "import_regime": "general_trade",
                        "rate_type": "mfn",
                        "tariff_rate": "0.10",
                        "vat_rate": "0.13",
                        "consumption_tax_rate": "0.20",
                        "insurance_cny": "15",
                        "provider": "licensed-customs-feed",
                        "source_reference": "CN tariff snapshot 2026",
                        "effective_date": "2026-01-01",
                    },
                }
            ],
        },
        "amazon",
    )

    assert len(candidates) == 1
    assert candidates[0].customs is not None
    assert candidates[0].customs.model_dump() == {
        "hs_code": "3303000010",
        "country_of_origin": "FR",
        "destination_country": "CN",
        "ship_from_country": "SG",
        "import_regime": "general_trade",
        "rate_type": "mfn",
        "tariff_rate": 0.10,
        "import_vat_rate": 0.13,
        "consumption_tax_rate": 0.20,
        "personal_postal_tax_rate": None,
        "personal_postal_assessed_value_cny": None,
        "personal_postal_total_value_cny": None,
        "personal_postal_value_limit_cny": None,
        "personal_postal_tax_exemption_threshold_cny": None,
        "personal_postal_single_indivisible_item": None,
        "personal_postal_eligible": None,
        "seller_collected_tax_cny": None,
        "insurance_cny": 15,
        "valuation": None,
        "cross_border_ecommerce_eligible": None,
        "provider": "licensed-customs-feed",
        "source_reference": "CN tariff snapshot 2026",
        "effective_date": "2026-01-01",
    }


def test_gateway_maps_provider_currency_conversion_quote() -> None:
    candidates = normalize_gateway_response(
        {
            "provider": "licensed-shopping-data-provider",
            "items": [
                {
                    "offer_id": "fx-offer",
                    "title": "Imported item",
                    "price": 99,
                    "currency": "usd",
                    "fx_quote": {
                        "source_currency": "usd",
                        "target_currency": "CNY",
                        "rate_to_cny": "7.2345",
                        "rate_type": "provider_quote",
                        "markup_status": "included",
                        "markup_bps": "35",
                        "provider": "licensed-fx-feed",
                        "source_reference": "quote/fx-123",
                        "observed_at": "2026-08-11T09:30:00+08:00",
                        "expires_at": "2026-08-11T10:00:00+08:00",
                    },
                }
            ],
        },
        "amazon",
    )

    assert len(candidates) == 1
    quote = candidates[0].price_conversion
    assert quote is not None
    assert quote.model_dump() == {
        "source_currency": "USD",
        "target_currency": "CNY",
        "rate_to_cny": 7.2345,
        "purpose": "comparison_estimate",
        "rate_type": "provider_quote",
        "markup_status": "included",
        "markup_bps": 35,
        "provider": "licensed-fx-feed",
        "source_reference": "quote/fx-123",
        "observed_at": "2026-08-11T01:30:00Z",
        "expires_at": "2026-08-11T02:00:00Z",
    }


def test_gateway_maps_route_specific_shipping_quote() -> None:
    candidates = normalize_gateway_response(
        {
            "provider": "licensed-shopping-data-provider",
            "items": [
                {
                    "offer_id": "shipping-offer",
                    "title": "Imported item",
                    "price": 99,
                    "currency": "USD",
                    "shipping_quote": {
                        "quote_type": "carrier_quote",
                        "currency": "USD",
                        "total_amount": "12.50",
                        "base_amount": "10",
                        "surcharge_amount": "3",
                        "discount_amount": "0.50",
                        "actual_weight_kg": "1.2",
                        "dimensional_weight_kg": "2.4",
                        "chargeable_weight_kg": "2.4",
                        "length_cm": "40",
                        "width_cm": "30",
                        "height_cm": "10",
                        "dimensional_divisor": "5000",
                        "origin_country": "us",
                        "destination_country": "cn",
                        "service_name": "International Priority",
                        "eta_min_days": "6",
                        "eta_max_days": "9",
                        "provider": "licensed-carrier-rate-feed",
                        "quote_id": "shipping/quote-123",
                        "quoted_at": "2026-08-11T09:00:00+08:00",
                        "valid_until": "2026-08-11T10:00:00+08:00",
                        "currency_conversion": {
                            "source_currency": "USD",
                            "rate_to_cny": "7.20",
                            "rate_type": "provider_quote",
                            "provider": "licensed-fx-feed",
                            "quote_id": "fx/shipping-123",
                            "observed_at": "2026-08-11T09:00:00+08:00",
                            "expires_at": "2026-08-11T10:00:00+08:00",
                        },
                    },
                }
            ],
        },
        "amazon",
    )

    quote = candidates[0].shipping_quote
    assert quote is not None
    assert quote.total_amount == 12.5
    assert quote.chargeable_weight_kg == 2.4
    assert quote.origin_country == "US"
    assert quote.destination_country == "CN"
    assert quote.eta_min_days == 6
    assert quote.eta_max_days == 9
    assert quote.observed_at == "2026-08-11T01:00:00Z"
    assert quote.expires_at == "2026-08-11T02:00:00Z"
    assert quote.currency_conversion is not None
    assert quote.currency_conversion.rate_to_cny == 7.2


def test_gateway_maps_customs_monthly_fx_and_cif_valuation() -> None:
    candidates = normalize_gateway_response(
        [
            {
                "offer_id": "customs-value-offer",
                "title": "Imported item",
                "price": 100,
                "currency": "USD",
                "customs": {
                    "hs_code": "8518300000",
                    "origin_country": "US",
                    "destination_country": "CN",
                    "import_regime": "general_trade",
                    "rate_type": "mfn",
                    "tariff_rate": 0.10,
                    "vat_rate": 0.13,
                    "insurance_cny": 10,
                    "provider": "licensed-customs-feed",
                    "source_reference": "CN tariff snapshot 2026",
                    "effective_date": "2026-08-01",
                    "valuation": {
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
                },
            }
        ],
        "amazon",
    )

    valuation = candidates[0].customs.valuation
    assert valuation is not None
    assert valuation.customs_value_cny == 800
    assert valuation.customs_conversion is not None
    assert valuation.customs_conversion.rate_to_cny == 7.1
    assert valuation.customs_conversion.assessment_month == "2026-08"
    assert valuation.provider == "licensed-customs-valuation-feed"


def test_gateway_maps_personal_postal_eligibility_and_exemption_policy() -> None:
    candidates = normalize_gateway_response(
        [
            {
                "title": "Personal parcel",
                "price": 250,
                "currency": "CNY",
                "customs": {
                    "hs_code": "8518300000",
                    "origin_country": "my",
                    "import_regime": "personal_postal",
                    "rate_type": "personal_postal",
                    "postal_tax_rate": "0.20",
                    "postal_assessed_value_cny": "250",
                    "postal_total_value_cny": "250",
                    "postal_value_limit_cny": "2000",
                    "postal_tax_exemption_threshold_cny": "50",
                    "postal_single_indivisible_item": "false",
                    "postal_eligible": "true",
                    "provider": "licensed-customs-feed",
                    "source_reference": "personal postal snapshot",
                    "effective_date": "2026-08-11",
                },
            }
        ],
        "shopee",
    )

    customs = candidates[0].customs
    assert customs is not None
    assert customs.personal_postal_assessed_value_cny == 250
    assert customs.personal_postal_total_value_cny == 250
    assert customs.personal_postal_value_limit_cny == 2000
    assert customs.personal_postal_tax_exemption_threshold_cny == 50
    assert customs.personal_postal_single_indivisible_item is False
    assert customs.personal_postal_eligible is True


def test_gateway_keeps_offer_but_drops_invalid_customs_evidence() -> None:
    candidates = normalize_gateway_response(
        [
            {
                "offer_id": "invalid-tax-evidence",
                "title": "Still useful product evidence",
                "price": 100,
                "currency": "CNY",
                "customs": {
                    "hs_code": "not-an-hs-code",
                    "origin_country": "FR",
                    "import_regime": "general_trade",
                },
            }
        ],
        "ebay",
    )

    assert len(candidates) == 1
    assert candidates[0].customs is None


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
    assert offer.customs is not None
    assert offer.customs.import_regime == "cross_border_ecommerce"
    assert offer.customs.cross_border_ecommerce_eligible is True
    assert offer.customs.provider == "deterministic-sandbox-tax-fixture"
    assert offer.customs.valuation is not None
    assert offer.customs.valuation.customs_conversion is not None
    assert offer.customs.valuation.customs_conversion.rate_basis == "monthly_customs_assessment"
    assert offer.customs.valuation.provider == "deterministic-sandbox-customs-valuation-fixture"
    assert offer.price_conversion is not None
    assert offer.price_conversion.source_currency == "USD"
    assert offer.price_conversion.rate_to_cny == 7.18
    assert offer.price_conversion.rate_type == "sandbox_fixture"
    assert offer.price_conversion.provider == "deterministic-sandbox-fx-fixture"
    assert offer.shipping_quote is not None
    assert offer.shipping_quote.quote_type == "sandbox_fixture"
    assert offer.shipping_quote.destination_country == "CN"
    assert offer.shipping_quote.chargeable_weight_kg == 0.34
    assert offer.shipping_quote.provider == "deterministic-sandbox-shipping-fixture"
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


async def test_data_provider_channel_credential_and_identity_are_preserved(monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "false")
    monkeypatch.setenv(
        "AMAZON_DATA_CHANNEL_ENDPOINT", "https://provider.example/channels/amazon/search"
    )
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_CREDENTIAL", "channel-credential")
    monkeypatch.setenv("AMAZON_DATA_PROVIDER", "purchased-catalog-provider")
    monkeypatch.setattr(httpx, "AsyncClient", _ProviderOmittingClient)

    result = await item_search("headphones", "amazon", top_k=1)

    assert _ProviderOmittingClient.request == {
        "endpoint": "https://provider.example/channels/amazon/search",
        "params": {"query": "headphones", "top_k": 1},
        "headers": {
            "Authorization": "Bearer channel-credential",
            "X-API-Key": "channel-credential",
        },
    }
    assert result.provider.provider == "purchased-catalog-provider"
    assert result.provider.status == "ok"
    assert result.candidates[0].provenance is not None
    assert result.candidates[0].provenance.provider == "purchased-catalog-provider"
