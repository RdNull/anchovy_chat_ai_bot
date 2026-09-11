"""`python -m src.blackbox` — serves the blackbox tools over streamable HTTP.

HTTP is the only transport. Local development runs this same server against localhost,
behind the same bearer check, so the auth path is exercised every day rather than only
in production.
"""

import sys

import uvicorn

from src import settings
from src.blackbox.app import build_app

if __name__ == '__main__':
    try:
        app = build_app(settings.BLACKBOX_MCP_ACCESS_TOKEN)
    except ValueError as exc:
        # One readable line on stderr and exit status 1: what `kubectl logs` shows for a
        # pod deployed with a blank token.
        sys.exit(str(exc))

    uvicorn.run(
        app,
        host=settings.BLACKBOX_HOST,
        port=settings.BLACKBOX_PORT,
        # `BearerAuth` logs every request itself, without the client address uvicorn adds.
        access_log=False,
        # Keep `src/logs.py`'s handler and its filters rather than uvicorn's own.
        log_config=None,
    )
