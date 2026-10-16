"""Same-origin browser client; only public OAuth configuration is anonymous."""

import base64
import hashlib
import re
from pathlib import Path
from uuid import UUID

from starlette.responses import HTMLResponse, JSONResponse

from mcp_lab.auth import Endpoint, ProtectedMCP


def register_web(mcp: ProtectedMCP, endpoint: Endpoint, client_id: str) -> None:
    client_id = str(UUID(client_id))
    assets = Path(__file__).parent / "static"

    def page(name: str) -> HTMLResponse:
        text = (assets / name).read_text(encoding="utf-8")

        def hashes(tag: str) -> str:
            bodies = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, flags=re.S | re.I)
            return " ".join(
                "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
                for body in bodies
            )

        return HTMLResponse(text, headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src {hashes('script')}; style-src {hashes('style')}; "
                "connect-src 'self' https://login.microsoftonline.com; "
                "frame-src https://login.microsoftonline.com; img-src 'self' data:; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        })

    @mcp.custom_route("/", methods=["GET"])
    async def index(request):
        return page("index.html")

    @mcp.custom_route("/auth.html", methods=["GET"])
    async def callback(request):
        return page("auth.html")

    @mcp.custom_route("/lab-config.json", methods=["GET"])
    async def configuration(request):
        return JSONResponse({
            "tenantId": endpoint.tenant,
            "clientId": client_id,
            "gatewayUrl": endpoint.url,
            "scope": endpoint.scope,
        }, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
