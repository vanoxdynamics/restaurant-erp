"""Network-level protection for /platform: optional CIDR allowlist and hardened response headers."""

import ipaddress
import json
from typing import List

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings
from app.core.errors import PayloadTooLarge

_HEADERS = {
    b"cache-control": b"no-store",
    b"pragma": b"no-cache",
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
    b"strict-transport-security": b"max-age=31536000; includeSubDomains",
}


class PlatformGuardMiddleware:
    """
    Applies to paths under `<API_V1_STR>/platform` only. Requests from outside PLATFORM_IP_ALLOWLIST get a plain
    404 (the console's existence is not advertised). With a reverse proxy, run uvicorn with --proxy-headers and
    --forwarded-allow-ips set to the proxy so `client.host` is the real caller; never trust X-Forwarded-For blindly.
    """

    def __init__(self, app: ASGIApp, networks: List[str] | None = None) -> None:
        self.app = app
        self.prefix = f"{settings.API_V1_STR}/platform"
        self.networks = [ipaddress.ip_network(n, strict=False) for n in (networks if networks is not None else settings.PLATFORM_IP_ALLOWLIST)]

    def _allowed(self, scope: Scope) -> bool:
        if not self.networks:
            return True
        client = scope.get("client")
        try:
            addr = ipaddress.ip_address(client[0]) if client else None
        except ValueError:
            return False
        return addr is not None and any(addr in net for net in self.networks)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == self.prefix or path.startswith(self.prefix + "/")):
            await self.app(scope, receive, send)
            return

        if not self._allowed(scope):
            body = json.dumps({"error": {"code": "not_found", "message": "Not found", "details": {}}}).encode()
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        limit = settings.PLATFORM_MAX_BODY_BYTES
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject_too_large(send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:  # chunked body with no (or a false) Content-Length
                    raise PayloadTooLarge("That file is too big. Please choose a smaller one.")
            return message

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in _HEADERS.items() if k not in existing
                ]
            await send(message)

        await self.app(scope, limited_receive, send_hardened)

    @staticmethod
    async def _reject_too_large(send: Send) -> None:
        body = json.dumps({"error": {"code": "payload_too_large", "message": "That file is too big. Please choose a smaller one.", "details": {}}}).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
