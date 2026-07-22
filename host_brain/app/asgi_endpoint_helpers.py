"""Small stateless adapters retained by the Host ASGI composition root."""

from __future__ import annotations

from fastapi import Response

from host_brain.app.app_factory import docs_redirect_response


async def root_endpoint() -> Response:
    """Redirect the Host root to the published API documentation."""

    return docs_redirect_response()


__all__ = ["root_endpoint"]
