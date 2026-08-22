"""Local UpstreamClientPort: forward with an async httpx client (offline-capable transport).

``httpx`` is a pure-Python core dependency (not a cloud SDK), so this adapter is used by the
SDK-free ``local`` profile and by ``gcp`` alike: the reverse-proxy transport is identical across
profiles, and only the identity-injection policy differs. The offline test suite never exercises a
live upstream (a recording fake is injected instead), so no network is required for the gate.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from ...config import Settings
from ...domain.models import UpstreamResponse

# Force identity encoding so the proxy forwards upstream bytes verbatim without ever having to
# re-compress a body whose ``content-encoding`` header it would otherwise have to keep in step.
_FORCE_IDENTITY = {"accept-encoding": "identity"}


class HttpxUpstreamClient:
    """Forward a single request to an upstream app and return its response bytes."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._settings.upstream_timeout_seconds),
                follow_redirects=False,
            )
        return self._client

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
    ) -> UpstreamResponse:
        client = self._get_client()
        response = await client.request(
            method,
            url,
            headers={**dict(headers), **_FORCE_IDENTITY},
            content=content,
        )
        return UpstreamResponse(
            status=response.status_code,
            headers=tuple(response.headers.items()),
            body=response.content,
            media_type=response.headers.get("content-type"),
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
