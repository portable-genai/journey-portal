"""Authenticated managed-profile transport for private Cloud Run upstreams."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from urllib.parse import urlsplit

from ...domain.models import UpstreamResponse
from ..local.upstream import HttpxUpstreamClient


class GcpUpstreamClient(HttpxUpstreamClient):
    """Attach a Google-signed ID token for the exact HTTPS upstream origin."""

    @staticmethod
    def _audience(url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("gcp upstream URLs must be absolute https URLs")
        return f"https://{parsed.netloc}"

    @staticmethod
    def _fetch_token(audience: str) -> str:  # pragma: no cover - needs workload identity
        from google.auth.transport.requests import Request
        from google.oauth2.id_token import fetch_id_token

        return str(fetch_id_token(Request(), audience))

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
    ) -> UpstreamResponse:
        audience = self._audience(url)
        token = await asyncio.to_thread(self._fetch_token, audience)
        managed_headers = dict(headers)
        managed_headers["authorization"] = f"Bearer {token}"
        return await super().forward(
            method=method,
            url=url,
            headers=managed_headers,
            content=content,
        )
