from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.schemas import (
    AlternativeCandidate,
    IdentityEvidence,
    IdentityEvidenceBasis,
    LandedCost,
)

_KNOWN_ATTRIBUTE_KEYS = {
    "capacity",
    "condition",
    "bundle",
    "bundle_contents",
    "color",
    "material",
    "region",
    "regional_version",
    "storage",
    "storage_gb",
}
_ATTRIBUTE_ALIASES = {
    "容量": "capacity",
    "capacity": "capacity",
    "存储": "capacity",
    "内存": "capacity",
    "storage": "capacity",
    "storagegb": "capacity",
    "storage_gb": "capacity",
    "成色": "condition",
    "condition": "condition",
    "状态": "condition",
    "套装": "bundle",
    "套餐": "bundle",
    "bundle": "bundle",
    "bundlecontents": "bundle_contents",
    "bundle_contents": "bundle_contents",
    "包装": "bundle",
    "颜色": "color",
    "颜色系": "color",
    "color": "color",
    "colour": "color",
    "材质": "material",
    "材料": "material",
    "material": "material",
    "materials": "material",
    "区域": "region",
    "地区": "region",
    "区域版本": "regional_version",
    "地区版本": "regional_version",
    "regionalversion": "regional_version",
    "regional_version": "regional_version",
    "region": "region",
}


@dataclass(frozen=True)
class ExactOfferClassification:
    matching_offers: list[LandedCost]
    alternative_candidates: list[AlternativeCandidate]


def _normal_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[\s_\-]+", "", normalized)


def _identity_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normal_text(value)
    return normalized or None


def _canonical_attribute_key(key: object) -> str:
    normalized = _normal_text(key)
    return _ATTRIBUTE_ALIASES.get(normalized, normalized)


def _variant_facts(offer: LandedCost) -> dict[str, str]:
    facts: dict[str, str] = {}
    for key, value in offer.variant_attributes.items():
        if value is not None:
            facts[_canonical_attribute_key(key)] = _normal_text(value)
    for key, value in offer.attributes.items():
        canonical = _canonical_attribute_key(key)
        if canonical in _KNOWN_ATTRIBUTE_KEYS and value is not None:
            facts.setdefault(canonical, _normal_text(value))
    return facts


def _identity_facts(offer: LandedCost) -> dict[str, str]:
    facts = _variant_facts(offer)
    for field in ("brand", "model"):
        value = _identity_value(getattr(offer.identity, field))
        if value is not None:
            facts[f"identity.{field}"] = value
    return facts


def _authority_identifier(offer: LandedCost) -> tuple[str, str] | None:
    gtin = _identity_value(offer.identity.gtin)
    if gtin is not None:
        return "identity.gtin", gtin
    mpn = _identity_value(offer.identity.mpn)
    if mpn is not None:
        return "identity.mpn", mpn
    return None


def _alternative(
    *,
    basis: IdentityEvidenceBasis = "insufficient",
    matched_fields: list[str] | None = None,
    missing_fields: list[str] | None = None,
    conflicting_fields: list[str] | None = None,
    explanation: str,
) -> IdentityEvidence:
    return IdentityEvidence(
        decision="alternative_candidate",
        basis=basis,
        matched_fields=matched_fields or [],
        missing_fields=missing_fields or [],
        conflicting_fields=conflicting_fields or [],
        explanation=explanation,
    )


def _material_comparison(
    reference: LandedCost,
    candidate: LandedCost,
    *,
    identifier_fields: list[str] | None = None,
) -> IdentityEvidence:
    reference_facts = _identity_facts(reference)
    candidate_facts = _identity_facts(candidate)
    fields = sorted(set(reference_facts) | set(candidate_facts))
    matched: list[str] = []
    missing: list[str] = []
    conflicting: list[str] = []
    for field in fields:
        reference_value = reference_facts.get(field)
        candidate_value = candidate_facts.get(field)
        if reference_value is None or candidate_value is None:
            missing.append(field)
        elif reference_value == candidate_value:
            matched.append(field)
        else:
            conflicting.append(field)

    if conflicting:
        return _alternative(
            matched_fields=matched,
            missing_fields=missing,
            conflicting_fields=conflicting,
            explanation="关键 Product Variant 属性不一致，不能证明是同款。",
        )
    if identifier_fields:
        return IdentityEvidence(
            decision="matching_offer",
            basis="identifier",
            matched_fields=[*identifier_fields, *matched],
            missing_fields=missing,
            conflicting_fields=[],
            explanation=f"{', '.join(identifier_fields)} 跨平台一致，且没有发现冲突的关键属性。",
        )
    required_core = {"identity.brand", "identity.model"}
    if missing or not required_core.issubset(matched):
        return _alternative(
            matched_fields=matched,
            missing_fields=sorted(set(missing) | (required_core - set(matched))),
            conflicting_fields=conflicting,
            explanation="缺少完整的品牌、型号或关键 Product Variant 属性证据。",
        )
    return IdentityEvidence(
        decision="matching_offer",
        basis="material_variant_attributes",
        matched_fields=matched,
        missing_fields=[],
        conflicting_fields=[],
        explanation="品牌、型号和全部已提供的关键 Product Variant 属性一致。",
    )


def match_offer_identity(reference: LandedCost, candidate: LandedCost) -> IdentityEvidence:
    """Compare two offers using only cross-platform identifiers and material attributes."""

    reference_identifier = _authority_identifier(reference)
    candidate_identifier = _authority_identifier(candidate)
    if reference_identifier is not None or candidate_identifier is not None:
        if reference_identifier is None or candidate_identifier is None:
            return _alternative(
                missing_fields=[
                    reference_identifier[0] if reference_identifier else "identity.identifier",
                    candidate_identifier[0] if candidate_identifier else "identity.identifier",
                ],
                explanation="仅有一侧提供权威跨平台 identifier，不能建立同款证明。",
            )
        if reference_identifier != candidate_identifier:
            return _alternative(
                conflicting_fields=[reference_identifier[0], candidate_identifier[0]],
                explanation="跨平台 identifier 不一致，不能建立同款证明。",
            )
        return _material_comparison(
            reference,
            candidate,
            identifier_fields=[reference_identifier[0]],
        )
    return _material_comparison(reference, candidate)


def _identity_strength(offer: LandedCost) -> tuple[int, int, int]:
    identifier = _authority_identifier(offer)
    facts = _identity_facts(offer)
    return (
        1 if identifier is not None else 0,
        int("identity.brand" in facts and "identity.model" in facts),
        len(facts),
    )


def _single_offer_evidence(offer: LandedCost) -> IdentityEvidence:
    facts = _identity_facts(offer)
    identifier = _authority_identifier(offer)
    if identifier is not None:
        missing = ["cross_platform_identifier"]
    else:
        missing = sorted({"identity.brand", "identity.model"} - set(facts))
    return _alternative(
        matched_fields=sorted(facts),
        missing_fields=missing,
        explanation="没有另一平台的独立商品证据可用于证明同款。",
    )


def _alternative_candidate(offer: LandedCost, evidence: IdentityEvidence) -> AlternativeCandidate:
    values = offer.model_dump()
    values["identity_evidence"] = evidence
    return AlternativeCandidate(
        **values,
        reason=f"相似商品候选：{evidence.explanation}",
    )


def classify_exact_offers(offers: Sequence[LandedCost]) -> ExactOfferClassification:
    """Select one deterministic identity group and keep every other offer separate."""

    if len(offers) < 2:
        return ExactOfferClassification(
            matching_offers=[],
            alternative_candidates=[
                _alternative_candidate(offer, _single_offer_evidence(offer)) for offer in offers
            ],
        )

    best_index = 0
    best_matches: list[tuple[int, IdentityEvidence]] = []
    best_key: tuple[int, tuple[int, int, int], int] | None = None
    for index, reference in enumerate(offers):
        matches = [
            (candidate_index, match_offer_identity(reference, candidate))
            for candidate_index, candidate in enumerate(offers)
            if candidate_index != index
        ]
        matching = [item for item in matches if item[1].decision == "matching_offer"]
        key = (len(matching), _identity_strength(reference), -index)
        if best_key is None or key > best_key:
            best_index = index
            best_matches = matching
            best_key = key

    if not best_matches:
        reference = offers[best_index]
        alternatives = []
        for index, candidate in enumerate(offers):
            evidence = (
                match_offer_identity(reference, candidate)
                if index != best_index
                else _single_offer_evidence(candidate)
            )
            alternatives.append(_alternative_candidate(candidate, evidence))
        return ExactOfferClassification(matching_offers=[], alternative_candidates=alternatives)

    evidence_by_index = {index: evidence for index, evidence in best_matches}
    first_match_evidence = best_matches[0][1]
    evidence_by_index[best_index] = first_match_evidence
    matching_offers = [
        offer.model_copy(update={"identity_evidence": evidence_by_index[index]})
        for index, offer in enumerate(offers)
        if index in evidence_by_index
    ]
    alternatives = [
        _alternative_candidate(
            offer,
            match_offer_identity(offers[best_index], offer),
        )
        for index, offer in enumerate(offers)
        if index not in evidence_by_index
    ]
    return ExactOfferClassification(
        matching_offers=matching_offers,
        alternative_candidates=alternatives,
    )
