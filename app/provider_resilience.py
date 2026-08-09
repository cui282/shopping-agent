from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

import httpx

from app.config import get_settings

T = TypeVar("T")


class ProviderCircuitOpenError(RuntimeError):
    """Raised when a provider is quarantined after repeated failures."""


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    half_open_in_flight: bool = False


def _retryable(error: BaseException) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


class ProviderResilience:
    """Per-provider retry, circuit-breaker and concurrency isolation.

    The registry is intentionally process-local. Deployments with multiple workers should use
    gateway-level circuit breaking as well; this layer keeps one failed marketplace from
    consuming every local task slot.
    """

    def __init__(self) -> None:
        self._states: dict[str, _CircuitState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._slots: dict[str, asyncio.Semaphore] = {}

    def _state(self, provider: str) -> _CircuitState:
        return self._states.setdefault(provider, _CircuitState())

    def _lock(self, provider: str) -> asyncio.Lock:
        return self._locks.setdefault(provider, asyncio.Lock())

    def _slot(self, provider: str) -> asyncio.Semaphore:
        settings = get_settings()
        current = self._slots.get(provider)
        if current is None:
            current = asyncio.Semaphore(settings.provider_max_concurrency)
            self._slots[provider] = current
        return current

    async def _before_call(self, provider: str) -> None:
        settings = get_settings()
        async with self._lock(provider):
            state = self._state(provider)
            if state.opened_at is None:
                return
            if monotonic() - state.opened_at < settings.provider_circuit_reset_seconds:
                raise ProviderCircuitOpenError(f"provider circuit is open: {provider}")
            if state.half_open_in_flight:
                raise ProviderCircuitOpenError(f"provider circuit is half-open: {provider}")
            state.half_open_in_flight = True

    async def _success(self, provider: str) -> None:
        async with self._lock(provider):
            state = self._state(provider)
            state.failures = 0
            state.opened_at = None
            state.half_open_in_flight = False

    async def _failure(self, provider: str) -> None:
        settings = get_settings()
        async with self._lock(provider):
            state = self._state(provider)
            state.failures += 1
            state.half_open_in_flight = False
            if state.failures >= settings.provider_circuit_failure_threshold:
                state.opened_at = monotonic()

    async def execute(self, provider: str, operation: Callable[[], Awaitable[T]]) -> T:
        settings = get_settings()
        await self._before_call(provider)
        async with self._slot(provider):
            attempts = max(1, settings.provider_retry_attempts + 1)
            for attempt in range(attempts):
                try:
                    result = await operation()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if not _retryable(exc) or attempt == attempts - 1:
                        await self._failure(provider)
                        raise
                    backoff = min(
                        settings.provider_retry_backoff_max_seconds,
                        settings.provider_retry_backoff_seconds * (2**attempt),
                    )
                    await asyncio.sleep(backoff + random.uniform(0, backoff * 0.25))
                else:
                    await self._success(provider)
                    return result
        raise RuntimeError("provider operation did not return")

    def reset(self) -> None:
        self._states.clear()
        self._locks.clear()
        self._slots.clear()


_registry = ProviderResilience()


def get_provider_resilience() -> ProviderResilience:
    return _registry


def reset_provider_resilience() -> None:
    _registry.reset()


__all__ = [
    "ProviderCircuitOpenError",
    "ProviderResilience",
    "get_provider_resilience",
    "reset_provider_resilience",
]
