"""The blackbox HTTP surface: bearer auth and probes in front of the MCP app.

The server is reachable from the internet and everything it returns is the chat, so the
auth check is a plain ASGI wrapper that runs before anything reads the request body. An
unauthenticated request never reaches the protocol layer. It is not a Starlette
`BaseHTTPMiddleware`, and it does not use the SDK's OAuth `token_verifier` path, which
answers with resource-metadata discovery this server does not serve.

Two paths are exempt, and neither returns data: `/healthz` answers liveness and touches
nothing, so a Mongo hiccup cannot restart the pod; `/readyz` pings both stores, so a
wrong read-only URI or a NetworkPolicy that blocks them fails the rollout instead of the
first tool call.
"""

import asyncio
import hmac
import logging
import time

from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src import mongo, settings
from src.blackbox.server import mcp
from src.embeddings.messages import messages_embeddings_client
from src.logs import logger

HEALTH_PATH = '/healthz'
READY_PATH = '/readyz'
_PROBE_PATHS = frozenset({HEALTH_PATH, READY_PATH})

# Under the probe's own timeout, so a hung store reads as "not ready" rather than as a
# probe that never answered.
_READY_TIMEOUT = 2

# Stateless mode builds and tears down a transport per request, and the SDK logs
# `Terminating session: None` at INFO for every one of them.
logging.getLogger('mcp.server.streamable_http').setLevel(logging.WARNING)


def build_app(token: str | None) -> ASGIApp:
    """The MCP app behind bearer auth. Refuses to build without a token.

    `env_ignore_empty=True` turns a blanked `BLACKBOX_MCP_ACCESS_TOKEN` into None, the
    same fall-through that left the mongo password empty for three months. Here the
    fall-through would be an unauthenticated server, so it is an error, not a default.
    """
    if not token or not token.strip():
        raise ValueError(
            'BLACKBOX_MCP_ACCESS_TOKEN is empty or unset — refusing to serve without auth'
        )

    inner = mcp.streamable_http_app(
        # The subdomain already says what this is; `mcp.../mcp` reads badly.
        streamable_http_path='/',
        # Sessionless: a pod restart or rollout strands no client session.
        stateless_http=True,
        # Plain JSON responses: no SSE stream for the proxy to buffer or time out.
        json_response=True,
        host=settings.BLACKBOX_HOST,
        # The SDK turns Host/Origin checks on only for localhost binds. The bearer token
        # is the control here: a rebinding page in a browser cannot attach it.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return BearerAuth(inner, token.strip())


class BearerAuth:
    """Serves the probes, and admits everything else only with the bearer token.

    Rejects with a bare 401 and `WWW-Authenticate: Bearer` without awaiting `receive`,
    so the body of a rejected request is never read. Logs method, path, status and
    duration, and never a header or a body: bodies are the chat.
    """

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'lifespan':
            # The MCP app's lifespan runs the session manager's task group.
            await self.app(scope, receive, send)
            return
        if scope['type'] != 'http':
            await send({'type': 'websocket.close', 'code': 1008})
            return

        started = time.monotonic()
        status = 0

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
            await send(message)

        path = scope['path']
        if path == HEALTH_PATH:
            await _empty(send_with_status, 200)
        elif path == READY_PATH:
            await _empty(send_with_status, 200 if await _stores_ready() else 503)
        elif self._authorized(scope):
            await self.app(scope, receive, send_with_status)
        else:
            await _empty(send_with_status, 401, [(b'www-authenticate', b'Bearer')])

        logger.log(
            logging.DEBUG if path in _PROBE_PATHS else logging.INFO,
            'BLACKBOX_HTTP method=%s path=%s status=%s elapsed_ms=%d',
            scope['method'], path, status, (time.monotonic() - started) * 1000,
        )

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope['headers']:
            if name == b'authorization':
                scheme, _, credentials = value.partition(b' ')
                return scheme.lower() == b'bearer' and hmac.compare_digest(
                    credentials.strip(), self._token,
                )
        return False


async def _empty(send: Send, status: int, headers: list[tuple[bytes, bytes]] | None = None):
    await send({
        'type': 'http.response.start',
        'status': status,
        'headers': [(b'content-length', b'0'), *(headers or [])],
    })
    await send({'type': 'http.response.body', 'body': b''})


async def _stores_ready() -> bool:
    """Both stores answer a read. Neither call creates anything.

    The cause goes to the log only: the response is an unauthenticated status code.
    """
    try:
        await asyncio.wait_for(mongo.db.command('ping'), _READY_TIMEOUT)
        await asyncio.wait_for(
            messages_embeddings_client.qdrant_client.get_collections(), _READY_TIMEOUT,
        )
    except Exception as exc:
        logger.warning('BLACKBOX_NOT_READY %s: %s', type(exc).__name__, exc)
        return False
    return True
