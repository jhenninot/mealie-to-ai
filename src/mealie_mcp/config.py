"""Configuration lue depuis les variables d'environnement."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    mealie_url: str
    mealie_api_token: str
    mcp_auth_token: str | None
    host: str = "0.0.0.0"
    port: int = 8000
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> Settings:
        url = os.environ.get("MEALIE_URL", "").rstrip("/")
        token = os.environ.get("MEALIE_API_TOKEN", "")
        if not url or not token:
            raise ConfigError("MEALIE_URL et MEALIE_API_TOKEN sont obligatoires.")

        auth = os.environ.get("MCP_AUTH_TOKEN") or None
        if auth is None and not _bool(os.environ.get("MCP_ALLOW_NO_AUTH")):
            raise ConfigError(
                "MCP_AUTH_TOKEN est obligatoire (ou MCP_ALLOW_NO_AUTH=true pour un usage purement local)."
            )
        if auth is not None and len(auth) < 24:
            raise ConfigError("MCP_AUTH_TOKEN doit faire au moins 24 caractères.")

        return cls(
            mealie_url=url,
            mealie_api_token=token,
            mcp_auth_token=auth,
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
            timeout=float(os.environ.get("MEALIE_TIMEOUT", "30")),
        )
