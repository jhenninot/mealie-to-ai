"""Application HTTP (Streamable HTTP MCP) avec authentification par jeton."""

from __future__ import annotations

import hmac
import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .mealie import MealieClient
from .server import build_server

MCP_PATH = "/mcp"
PUBLIC_PATHS = {"/health"}


class TokenAuthMiddleware:
    """Protège l'endpoint MCP par un jeton secret.

    Deux façons de le fournir :
    - dans l'URL : ``/mcp/<jeton>`` — pour les connecteurs personnalisés claude.ai, qui ne
      permettent pas d'ajouter d'en-tête ;
    - en en-tête : ``Authorization: Bearer <jeton>`` sur ``/mcp`` — pour Claude Code, etc.
    """

    def __init__(self, app: ASGIApp, token: str | None):
        self.app = app
        self.token = token

    def _valid(self, candidate: str) -> bool:
        return self.token is not None and hmac.compare_digest(candidate.encode(), self.token.encode())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.token is None or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        prefix = MCP_PATH + "/"
        if path.startswith(prefix) and self._valid(path[len(prefix) :].rstrip("/")):
            scope = dict(scope, path=MCP_PATH, raw_path=MCP_PATH.encode())
            await self.app(scope, receive, send)
            return

        if path.rstrip("/") == MCP_PATH:
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if auth[:7].lower() == "bearer " and self._valid(auth[7:].strip()):
                await self.app(scope, receive, send)
                return

        body = json.dumps({"error": "unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401 if path.startswith(MCP_PATH) else 404,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(settings: Settings, mealie: MealieClient | None = None) -> ASGIApp:
    mealie = mealie or MealieClient(settings.mealie_url, settings.mealie_api_token, timeout=settings.timeout)
    mcp = build_server(mealie)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        # Sans état : pas de session perdue au redémarrage du conteneur.
        stateless_http=True,
        json_response=True,
        # Le serveur est derrière un reverse proxy : la protection DNS-rebinding
        # « localhost uniquement » bloquerait les requêtes venant de claude.ai.
        host=settings.host,
    )
    return TokenAuthMiddleware(app, settings.mcp_auth_token)
