"""router.py: role -> ordered provider chain, with failover on transport errors only.

``resolve(role)`` answers (row, key) for the role's primary provider. ``chat``
walks the chain: a connect failure, a timeout, a 5xx or a 429 moves to the next
row; anything else stops there. A 4xx is the provider telling the operator
something (bad key, unknown model) and a bad answer is the Executive's single
repair attempt to make, so neither is hidden behind a fallback.

The store is re-read whenever its fingerprint (mtime, size) changes, so a save
from the Models page applies to the next mission. Keys reach the HTTP client
through the env var each row names; error text is redacted before it leaves.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Union

import httpx

from . import wire
from .providers import ProviderRow, ProviderStore, ProviderStoreError, export_keys, fingerprint, load_store

FAILOVER_STATUS = frozenset({429})


class RouterError(RuntimeError):
    """Base class. Messages are already redacted."""


class NotConfigured(RouterError):
    pass


class ProviderHTTPError(RouterError):
    """A non-failover HTTP status from one provider (4xx other than 429)."""

    def __init__(self, message: str, row: ProviderRow, status: int, attempts: list):
        super().__init__(message)
        self.row = row
        self.status = status
        self.attempts = attempts


class ProviderResponseError(RouterError):
    """A 2xx whose body is not JSON. Carries the (redacted) raw text for repair."""

    def __init__(self, message: str, row: ProviderRow, raw: str, attempts: list):
        super().__init__(message)
        self.row = row
        self.raw = raw
        self.attempts = attempts


class TransportFailure(RouterError):
    """Every provider in the chain failed at the transport level."""

    def __init__(self, message: str, row: ProviderRow, attempts: list):
        super().__init__(message)
        self.row = row
        self.attempts = attempts


@dataclass(frozen=True)
class Candidate:
    row: ProviderRow
    key: str = field(default="", repr=False)


@dataclass
class ChatResult:
    row: ProviderRow
    data: dict
    latency_ms: float
    fallback_used: bool
    failovers: list = field(default_factory=list)


def is_failover_status(status: int) -> bool:
    return status in FAILOVER_STATUS or status >= 500


class Router:
    def __init__(self, path: Optional[Union[str, Path]] = None, legacy: Any = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._path = path
        self.legacy = legacy
        self._transport = transport
        self._store: Optional[ProviderStore] = None
        self._store_error: Optional[str] = None
        self._fingerprint: Any = ()  # () never equals a real fingerprint or None
        self._exported: dict = {}

    @property
    def transport(self) -> Optional[httpx.AsyncBaseTransport]:
        """The injected transport (tests), shared with the probe."""
        return self._transport

    # -- the store, re-read on change ------------------------------------------------------
    def store(self) -> ProviderStore:
        current = fingerprint(self._path)
        if current != self._fingerprint or (self._store is None and self._store_error is None):
            self._fingerprint = current
            try:
                self._store = load_store(self._path, legacy=self.legacy)
                self._store_error = None
            except ProviderStoreError as exc:
                self._store, self._store_error = None, str(exc)
                self._exported = export_keys(ProviderStore(), self._exported)
            else:
                self._exported = export_keys(self._store, self._exported)
        if self._store is None:
            raise NotConfigured(f"LLM provider store not configured: {self._store_error}")
        return self._store

    def chain(self, role: str) -> list:
        rows = self.store().chain(role)
        return [Candidate(row, os.environ.get(row.key_env, "")) for row in rows]

    def resolve(self, role: str) -> tuple:
        chain = self.chain(role)
        if not chain:
            raise NotConfigured(f"LLM role {role!r} is not configured: no provider is bound to it "
                                "(Models page, ~/.hive/providers.yaml, or QWEN_BASE_URL)")
        return chain[0].row, chain[0].key

    # -- HTTP ----------------------------------------------------------------------------
    def client(self, row: ProviderRow, key: str, timeout_s: Optional[float] = None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return httpx.AsyncClient(base_url=row.base_url, headers=headers,
                                 timeout=timeout_s or row.sampling.timeout_s, transport=self._transport)

    async def chat(self, role: str, build_body: Callable[[ProviderRow], dict]) -> ChatResult:
        candidates = self.chain(role)
        if not candidates:
            self.resolve(role)  # raises NotConfigured with the explanation
        attempts: list[str] = []
        for index, cand in enumerate(candidates):
            row, key = cand.row, cand.key
            t0 = time.perf_counter()
            try:
                async with self.client(row, key) as http:
                    response = await http.post(wire.chat_path(row.kind), json=build_body(row))
            except httpx.TransportError as exc:
                attempts.append(f"{row.id}: {wire.redact(wire.transport_error_text(exc), key)}")
                if index + 1 < len(candidates):
                    continue
                raise TransportFailure("every provider for role "
                                       f"{role!r} failed: " + "; ".join(attempts), row, attempts) from None
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            if response.status_code >= 400:
                text = wire.redact(wire.provider_error_text(response), key)
                message = f"HTTP {response.status_code} from {row.id}: {text}"
                if is_failover_status(response.status_code):
                    attempts.append(f"{row.id}: {message}")
                    if index + 1 < len(candidates):
                        continue
                    raise TransportFailure("every provider for role "
                                           f"{role!r} failed: " + "; ".join(attempts), row, attempts)
                raise ProviderHTTPError(message, row, response.status_code, attempts)
            try:
                data = response.json()
            except ValueError:
                raw = wire.redact(response.text, key)
                raise ProviderResponseError(f"{row.id} answered HTTP {response.status_code} with a body "
                                            "that is not JSON", row, raw, attempts) from None
            if not isinstance(data, dict):
                raise ProviderResponseError(f"{row.id} answered with JSON that is not an object", row,
                                            wire.redact(response.text, key), attempts)
            return ChatResult(row=row, data=data, latency_ms=latency_ms, fallback_used=index > 0,
                              failovers=list(attempts))
        raise NotConfigured(f"LLM role {role!r} is not configured")  # unreachable: empty chain raised above
