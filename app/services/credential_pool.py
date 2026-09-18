"""Concurrency-safe, secret-redacting provider credential rotation."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


class CredentialsUnavailable(RuntimeError):
    """No configured credential is currently eligible for selection."""


@dataclass(frozen=True)
class Credential:
    """A numbered credential lease whose representation never reveals its value."""

    number: int
    _value: str = field(repr=False)

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Credential(number={self.number}, value=<redacted>)"


class CredentialPool:
    """Select one credential per attempt using lock-protected round robin."""

    def __init__(
        self,
        values: Sequence[str],
        *,
        cooldown_seconds: float = 60.0,
    ) -> None:
        numbered = {
            index: value
            for index, value in enumerate(values, start=1)
            if value and value.strip()
        }
        self._initialize(numbered, cooldown_seconds)

    @classmethod
    def from_numbered(
        cls,
        values: Mapping[int, str],
        *,
        cooldown_seconds: float = 60.0,
    ) -> CredentialPool:
        pool = cls.__new__(cls)
        pool._initialize(values, cooldown_seconds)
        return pool

    def _initialize(self, values: Mapping[int, str], cooldown_seconds: float) -> None:
        self._credentials = tuple(
            Credential(number, value.strip())
            for number, value in sorted(values.items())
            if value and value.strip()
        )
        if not self._credentials:
            raise CredentialsUnavailable("no usable credentials are configured")
        self._cooldown_seconds = cooldown_seconds
        self._unhealthy_until: dict[int, float] = {}
        self._cursor = 0
        self._lock = threading.Lock()

    def select(self, *, now: float | None = None) -> Credential:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            for _ in range(len(self._credentials)):
                credential = self._credentials[self._cursor]
                self._cursor = (self._cursor + 1) % len(self._credentials)
                if self._unhealthy_until.get(credential.number, 0) <= current_time:
                    self._unhealthy_until.pop(credential.number, None)
                    return credential
        raise CredentialsUnavailable("no healthy credentials are currently available")

    def mark_unhealthy(self, credential: Credential, *, now: float | None = None) -> None:
        current_time = time.monotonic() if now is None else now
        with self._lock:
            self._unhealthy_until[credential.number] = current_time + self._cooldown_seconds

    def __len__(self) -> int:
        return len(self._credentials)

    def __repr__(self) -> str:
        return f"CredentialPool(size={len(self._credentials)})"
