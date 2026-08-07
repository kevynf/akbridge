"""Minimal environment-backed credential handling for AKBridge deployments."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .reliability import redact_secrets


@dataclass(frozen=True, slots=True)
class CredentialStore:
    """Read credentials from explicitly named environment variables only."""

    environ: Mapping[str, str] = os.environ
    prefix: str = "AKBRIDGE_"

    def get(self, name: str, default: str | None = None) -> str | None:
        key = name if name.isupper() else f"{self.prefix}{name.upper()}"
        return self.environ.get(key, default)

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise RuntimeError(f"missing required credential: {name}")
        return value

    def snapshot(self) -> dict[str, str]:
        """Return only key names and redacted values for diagnostics."""
        values = {
            key: value
            for key, value in self.environ.items()
            if key.startswith(self.prefix)
            and any(
                token in key.casefold()
                for token in ("token", "secret", "key", "password", "cookie")
            )
        }
        return redact_secrets(values)
