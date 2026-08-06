from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.recall import tower_item, tower_query
from app.recall.ann import FaissANN
from app.schemas import (
    Candidate,
    CategoryInsightOutput,
    ProviderMetadata,
    RecallChannelName,
    RecallChannelReport,
    RecallProvenance,
    RecallReadiness,
    RecallResult,
)


class QueryTower(Protocol):
    async def encode(self, query: str) -> list[float]: ...


class ItemTower(Protocol):
    async def encode(self, item: Candidate) -> list[float]: ...


class ANNIndex(Protocol):
    def search(
        self, vector: list[float], top_k: int = 20
    ) -> tuple[list[float], list[int]] | Awaitable[tuple[list[float], list[int]]]: ...


@dataclass(slots=True)
class HTTPQueryTower:
    async def encode(self, query: str) -> list[float]:
        return await tower_query.encode_query(query)


@dataclass(slots=True)
class HTTPItemTower:
    async def encode(self, item: Candidate) -> list[float]:
        return await tower_item.encode_item(item.model_dump(mode="json"))


@dataclass(slots=True)
class FaissIndex:
    index: FaissANN | None = None

    async def search(self, vector: list[float], top_k: int = 20) -> tuple[list[float], list[int]]:
        index = self.index
        if index is None:
            index = FaissANN(get_settings().ann_index_path)
            self.index = index
        return await asyncio.to_thread(index.search, vector, top_k)


@dataclass(slots=True)
class RecallAdapters:
    """Typed injection point for optional recall adapters and deterministic fakes."""

    query_tower: QueryTower = field(default_factory=HTTPQueryTower)
    item_tower: ItemTower = field(default_factory=HTTPItemTower)
    ann: ANNIndex = field(default_factory=FaissIndex)


def _configured_report(
    channel: RecallChannelName,
    configured: bool,
    *,
    reason_code: str = "configured_not_probed",
    reason: str = "channel is configured; runtime health is checked during research",
    unconfigured_reason_code: str = "not_configured",
    unconfigured_reason: str = "channel is not configured; deterministic fallback remains active",
) -> RecallChannelReport:
    return RecallChannelReport(
        channel=channel,
        configured=configured,
        state="configured" if configured else "unavailable",
        reason_code=reason_code if configured else unconfigured_reason_code,
        reason=reason if configured else unconfigured_reason,
    )


def _configured_channels(settings: Settings) -> dict[RecallChannelName, RecallChannelReport]:
    ann_enabled = settings.ann_backend == "faiss"
    return {
        "opensearch": _configured_report(
            "opensearch",
            bool(settings.opensearch_url),
            reason="OpenSearch category knowledge is configured; runtime health is checked during research",
        ),
        "query_tower": _configured_report(
            "query_tower",
            ann_enabled and bool(settings.tower_query_endpoint),
            reason_code="configured_not_probed" if ann_enabled else "ann_backend_disabled",
            reason=(
                "query tower is configured for ANN recall; runtime health is checked during research"
                if ann_enabled
                else "ANN backend is disabled; query tower is not used for item recall"
            ),
            unconfigured_reason_code=(
                "endpoint_not_configured" if ann_enabled else "ann_backend_disabled"
            ),
            unconfigured_reason=(
                "TOWER_QUERY_ENDPOINT is not configured; deterministic fallback remains active"
                if ann_enabled
                else "ANN backend is disabled; deterministic fallback remains active"
            ),
        ),
        "item_tower": _configured_report(
            "item_tower",
            ann_enabled and bool(settings.tower_item_endpoint),
            reason_code="configured_not_probed" if ann_enabled else "ann_backend_disabled",
            reason=(
                "item tower is configured for ANN recall; runtime health is checked during research"
                if ann_enabled
                else "ANN backend is disabled; item tower is not used for item recall"
            ),
            unconfigured_reason_code=(
                "endpoint_not_configured" if ann_enabled else "ann_backend_disabled"
            ),
            unconfigured_reason=(
                "TOWER_ITEM_ENDPOINT is not configured; deterministic fallback remains active"
                if ann_enabled
                else "ANN backend is disabled; deterministic fallback remains active"
            ),
        ),
        "faiss": _configured_report(
            "faiss",
            ann_enabled and bool(settings.ann_index_path),
            reason_code="configured_not_probed" if ann_enabled else "backend_disabled",
            reason=(
                "Faiss index is configured; index health is checked during research"
                if ann_enabled
                else "ANN_BACKEND is disabled; deterministic fallback remains active"
            ),
            unconfigured_reason_code="index_not_configured" if ann_enabled else "backend_disabled",
            unconfigured_reason=(
                "ANN_INDEX_PATH is not configured; deterministic fallback remains active"
                if ann_enabled
                else "ANN_BACKEND is disabled; deterministic fallback remains active"
            ),
        ),
    }


def _mode_for_channels(
    channels: dict[RecallChannelName, RecallChannelReport],
) -> str:
    if all(report.state == "configured" and report.configured for report in channels.values()):
        return "hybrid"
    if any(report.configured for report in channels.values()):
        return "partial_hybrid"
    return "deterministic_fallback"


def recall_readiness(settings: Settings | None = None) -> RecallReadiness:
    """Describe configured recall channels without pretending that they were probed."""

    effective = settings or get_settings()
    channels = _configured_channels(effective)
    return RecallReadiness(
        mode=_mode_for_channels(channels),  # type: ignore[arg-type]
        channels=channels,
        required_actions=list(effective.recall_required_actions),
    )


def _reason_code(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, LookupError):
        return "empty_response"
    if isinstance(exc, (TypeError, ValueError)):
        return "invalid_response"
    return "channel_failed"


def _vector(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("embedding must be a non-empty vector")
    vector = [float(item) for item in value]
    if not all(math.isfinite(item) for item in vector):
        raise ValueError("embedding contains a non-finite value")
    return vector


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _category_score(candidate: Candidate, insight: CategoryInsightOutput | None) -> float:
    if insight is None or insight.provider.source != "live":
        return 0.0
    haystack = " ".join(
        [
            candidate.title,
            insight.category,
            *candidate.attributes.keys(),
            *(str(value) for value in candidate.attributes.values()),
        ]
    ).casefold()
    terms = [term.casefold() for term in insight.components if term.strip()]
    if not terms:
        return 0.0
    return sum(term in haystack for term in terms) / len(terms)


def _set_channel(
    channels: dict[RecallChannelName, RecallChannelReport],
    channel: RecallChannelName,
    *,
    state: str,
    reason_code: str,
    reason: str,
    participated: bool = False,
) -> None:
    channels[channel] = channels[channel].model_copy(
        update={
            "state": state,
            "reason_code": reason_code,
            "reason": reason,
            "participated": participated,
        }
    )


async def _await_with_timeout(value: Any, timeout: float) -> Any:
    if hasattr(value, "__await__"):
        return await asyncio.wait_for(value, timeout=timeout)
    return value


@dataclass(slots=True)
class RecallOrchestrator:
    adapters: RecallAdapters = field(default_factory=RecallAdapters)

    async def _query_vector(
        self,
        query: str,
        settings: Settings,
        channels: dict[RecallChannelName, RecallChannelReport],
    ) -> list[float] | None:
        report = channels["query_tower"]
        if not report.configured:
            return None
        try:
            encoded = await _await_with_timeout(
                self.adapters.query_tower.encode(query), settings.recall_timeout_seconds
            )
            vector = _vector(encoded)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - optional channel failure is disclosed
            _set_channel(
                channels,
                "query_tower",
                state="unavailable",
                reason_code=_reason_code(exc),
                reason=f"query tower failed: {_reason_code(exc)}",
            )
            return None
        _set_channel(
            channels,
            "query_tower",
            state="ready",
            reason_code="ready",
            reason="query embedding returned a finite vector",
        )
        return vector

    async def _item_vectors(
        self,
        candidates: list[Candidate],
        settings: Settings,
        channels: dict[RecallChannelName, RecallChannelReport],
    ) -> dict[int, list[float]]:
        report = channels["item_tower"]
        if not report.configured:
            return {}
        if not candidates:
            _set_channel(
                channels,
                "item_tower",
                state="degraded",
                reason_code="empty_candidate_set",
                reason="marketplace Product Evidence was empty",
            )
            return {}

        async def encode(
            index: int, candidate: Candidate
        ) -> tuple[int, list[float] | None, str | None]:
            try:
                encoded = await _await_with_timeout(
                    self.adapters.item_tower.encode(candidate),
                    settings.recall_timeout_seconds,
                )
                return index, _vector(encoded), None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one optional item vector may fail
                return index, None, _reason_code(exc)

        results = await asyncio.gather(
            *(encode(index, candidate) for index, candidate in enumerate(candidates)),
            return_exceptions=True,
        )
        vectors: dict[int, list[float]] = {}
        failures: list[str] = []
        for result in results:
            if isinstance(result, BaseException):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                failures.append(_reason_code(result))
                continue
            index, vector, failure = result
            if vector is not None:
                vectors[index] = vector
            if failure is not None:
                failures.append(failure)
        if not failures and len(vectors) == len(candidates):
            _set_channel(
                channels,
                "item_tower",
                state="ready",
                reason_code="ready",
                reason="item embeddings returned for every Product Evidence candidate",
            )
        elif vectors:
            _set_channel(
                channels,
                "item_tower",
                state="degraded",
                reason_code="partial_response",
                reason="item tower returned embeddings for only part of Product Evidence",
            )
        else:
            _set_channel(
                channels,
                "item_tower",
                state="unavailable",
                reason_code=failures[0] if failures else "empty_response",
                reason="item tower returned no usable embeddings",
            )
        return vectors

    async def _ann_hits(
        self,
        query_vector: list[float] | None,
        item_vectors: dict[int, list[float]],
        candidates: list[Candidate],
        settings: Settings,
        channels: dict[RecallChannelName, RecallChannelReport],
    ) -> tuple[list[tuple[float, int]] | None, str | None]:
        report = channels["faiss"]
        if not report.configured:
            return None, None
        if query_vector is None:
            _set_channel(
                channels,
                "faiss",
                state="unavailable",
                reason_code="query_tower_unavailable",
                reason="Faiss recall requires a usable query embedding",
            )
            return None, "query_tower_unavailable"
        if len(item_vectors) != len(candidates):
            _set_channel(
                channels,
                "faiss",
                state="unavailable",
                reason_code="item_tower_unavailable",
                reason="Faiss recall requires an item embedding for every candidate",
            )
            return None, "item_tower_unavailable"
        try:
            raw_scores, raw_ids = await _await_with_timeout(
                self.adapters.ann.search(query_vector, top_k=max(1, len(candidates))),
                settings.recall_timeout_seconds,
            )
            if not isinstance(raw_scores, (list, tuple)) or not isinstance(raw_ids, (list, tuple)):
                raise TypeError("ANN response must contain score and id lists")
            if len(raw_scores) != len(raw_ids):
                raise ValueError("ANN scores and ids have different lengths")
            hits = [
                (float(score), int(item_id))
                for score, item_id in zip(raw_scores, raw_ids)
                if math.isfinite(float(score)) and int(item_id) >= 0
            ]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - optional channel failure is disclosed
            reason_code = _reason_code(exc)
            _set_channel(
                channels,
                "faiss",
                state="unavailable",
                reason_code=reason_code,
                reason=f"Faiss index failed: {reason_code}",
            )
            return None, reason_code
        if not hits:
            _set_channel(
                channels,
                "faiss",
                state="degraded",
                reason_code="empty_response",
                reason="Faiss returned no candidate IDs; deterministic fallback preserved Product Evidence",
            )
            return None, "empty_response"
        _set_channel(
            channels,
            "faiss",
            state="ready",
            reason_code="ready",
            reason="Faiss returned candidate IDs and similarity scores",
        )
        return hits, None

    def _select(
        self,
        candidates: list[Candidate],
        category_insight: CategoryInsightOutput | None,
        query_vector: list[float] | None,
        item_vectors: dict[int, list[float]],
        ann_hits: list[tuple[float, int]] | None,
        top_k: int,
    ) -> tuple[list[Candidate], set[RecallChannelName], str | None]:
        category_scores = {
            index: _category_score(candidate, category_insight)
            for index, candidate in enumerate(candidates)
        }
        item_scores: dict[int, float] = {}
        if query_vector is not None:
            for index, vector in item_vectors.items():
                try:
                    item_scores[index] = _cosine(query_vector, vector)
                except ValueError:
                    continue

        if ann_hits:
            mapped: list[tuple[float, int]] = []
            seen: set[int] = set()
            for score, index in ann_hits:
                if index >= len(candidates) or index in seen:
                    continue
                seen.add(index)
                mapped.append((score, index))
            if mapped:
                mapped.sort(
                    key=lambda hit: (
                        -hit[0],
                        -item_scores.get(hit[1], 0.0),
                        -category_scores.get(hit[1], 0.0),
                        hit[1],
                    )
                )
                selected = [candidates[index] for _, index in mapped[:top_k]]
                return selected, {"opensearch", "query_tower", "item_tower", "faiss"}, None
            return candidates, set(), "faiss_id_mapping_failed"

        if any(score > 0 for score in category_scores.values()):
            ordered = sorted(
                range(len(candidates)),
                key=lambda index: (-category_scores[index], index),
            )
            selected = [candidates[index] for index in ordered[:top_k]]
            return selected, {"opensearch"}, None

        return candidates, set(), None

    async def recall(
        self,
        query: str,
        candidates: list[Candidate],
        *,
        category_insight: CategoryInsightOutput | None,
        top_k: int | None = None,
    ) -> RecallResult:
        """Select or order only candidates already supplied as Product Evidence."""

        settings = get_settings()
        candidate_pool = list(candidates)
        limit = max(1, min(top_k or len(candidate_pool) or 1, len(candidate_pool) or 1))
        readiness = recall_readiness(settings)
        channels = {
            name: report.model_copy(deep=True) for name, report in readiness.channels.items()
        }

        opensearch = channels["opensearch"]
        if opensearch.configured:
            if category_insight is not None and category_insight.provider.source == "live":
                _set_channel(
                    channels,
                    "opensearch",
                    state="ready" if category_insight.provider.status == "ok" else "degraded",
                    reason_code=(
                        "ready" if category_insight.provider.status == "ok" else "semantic_fallback"
                    ),
                    reason=(
                        "OpenSearch category knowledge was returned"
                        if category_insight.provider.status == "ok"
                        else "OpenSearch returned category knowledge with a disclosed semantic fallback"
                    ),
                )
            else:
                _set_channel(
                    channels,
                    "opensearch",
                    state="degraded",
                    reason_code="request_failed",
                    reason="OpenSearch was configured but category knowledge used deterministic fallback",
                )

        query_vector: list[float] | None = None
        item_vectors: dict[int, list[float]] = {}
        ann_hits: list[tuple[float, int]] | None = None
        ann_failure: str | None = None
        if not candidate_pool:
            for name in ("query_tower", "item_tower", "faiss"):
                if channels[name].configured:
                    _set_channel(
                        channels,
                        name,
                        state="degraded",
                        reason_code="empty_candidate_set",
                        reason="marketplace Product Evidence was empty",
                    )
        elif settings.ann_backend == "faiss":
            query_task = asyncio.create_task(self._query_vector(query, settings, channels))
            item_task = asyncio.create_task(self._item_vectors(candidate_pool, settings, channels))
            query_vector, item_vectors = await asyncio.gather(query_task, item_task)
            ann_hits, ann_failure = await self._ann_hits(
                query_vector,
                item_vectors,
                candidate_pool,
                settings,
                channels,
            )
        selected, used_channels, selection_failure = self._select(
            candidate_pool,
            category_insight,
            query_vector,
            item_vectors,
            ann_hits,
            limit,
        )
        if selection_failure is not None:
            _set_channel(
                channels,
                "faiss",
                state="degraded",
                reason_code=selection_failure,
                reason="Faiss IDs did not map to the current Product Evidence set",
            )

        for name in used_channels:
            report = channels[name]
            if report.state in {"ready", "degraded"}:
                channels[name] = report.model_copy(update={"participated": True})
        participating = [name for name, report in channels.items() if report.participated]
        all_ready = all(
            channels[name].state == "ready" and channels[name].participated for name in channels
        )
        if all_ready:
            mode = "hybrid"
        elif participating:
            mode = "partial_hybrid"
        else:
            mode = "deterministic_fallback"
        fallback_reason = None
        if mode == "deterministic_fallback":
            fallback_reason = (
                f"faiss_{ann_failure}" if ann_failure else "optional_recall_unavailable"
            )
        elif mode == "partial_hybrid":
            failures = [
                f"{name}_{report.reason_code}"
                for name, report in channels.items()
                if report.state in {"degraded", "unavailable"}
            ]
            fallback_reason = failures[0] if failures else "partial_channel_failure"

        provenance = RecallProvenance(
            mode=mode,
            channels=channels,
            participating_channels=participating,
            fallback_reason=fallback_reason,
            input_candidate_count=len(candidate_pool),
            selected_candidate_count=len(selected),
        )
        status = "ok" if mode == "hybrid" else "degraded"
        return RecallResult(
            candidates=selected,
            total_recall=len(selected),
            truncated=len(selected) < len(candidate_pool),
            provenance=provenance,
            provider=ProviderMetadata(
                source="computed",
                provider="recall-orchestrator",
                status=status,
                fallback_reason=fallback_reason,
            ),
        )
