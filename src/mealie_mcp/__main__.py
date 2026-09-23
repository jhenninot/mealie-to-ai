"""Point d'entrée : ``python -m mealie_mcp`` ou ``mealie-mcp``."""

from __future__ import annotations

import sys

import uvicorn

from .app import create_app
from .config import ConfigError, Settings


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        sys.exit(2)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
