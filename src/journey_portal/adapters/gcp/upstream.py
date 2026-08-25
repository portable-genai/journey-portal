"""Authenticated managed-profile transport for private Cloud Run upstreams."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Mapping
from urllib.parse import urlsplit

from ...domain.models import UpstreamResponse
from ..local.upstream import HttpxUpstreamClient

_LOGGER = logging.getLogger(__name__)


def _readable_claims(token: str) -> str:
    """The non-secret claims of a JWT, for a log line. Never the signature, never the token."""

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, binascii.Error, json.JSONDecodeError):
        return "unreadable (not a compact JWS)"
    return json.dumps({k: claims.get(k) for k in ("aud", "iss", "email", "azp", "exp")})


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
        try:
            token = await asyncio.to_thread(self._fetch_token, audience)
        except Exception as exc:
            # A service-to-service call that cannot mint its credential must say so. Left
            # unlogged this surfaced as the UPSTREAM's own 401 passed through to the browser,
            # which points the reader at the callee's IAM rather than at the caller's identity.
            _LOGGER.error(
                "could not mint a service ID token for %s: %s: %s",
                audience,
                type(exc).__name__,
                exc,
            )
            raise
        managed_headers = dict(headers)
        managed_headers["authorization"] = f"Bearer {token}"
        response = await super().forward(
            method=method,
            url=url,
            headers=managed_headers,
            content=content,
        )
        if response.status in {401, 403}:
            # The token's own claims, never the token. aud/iss/email are the three that decide
            # whether Cloud Run accepts it, and reading them off the wire is the only way to
            # tell "minted for the wrong audience" from "the caller is not allowed" -- the two
            # produce the same status and the same opaque body.
            _LOGGER.warning("portal service token claims: %s", _readable_claims(token))
            # The upstream refused the portal's OWN credential. Distinguishable in the log from
            # the portal refusing the browser, which is the same status code arriving at the
            # client from a different hop entirely.
            _LOGGER.warning(
                "upstream %s refused the portal service token: %s %s -> %s (audience %s)",
                audience,
                method,
                url,
                response.status,
                audience,
            )
            _LOGGER.warning("upstream refusal body (first 300 bytes): %r", response.body[:300])
        return response
