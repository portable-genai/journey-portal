"""Platform profile delivers content-free portal access evidence to agent-observability."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx
from hex_service_kit.s2s import validate_base_url

from ...config import Settings
from ...domain.models import PortalAccessEvent
from ...domain.observability_audit import to_observability_audit_event
from ...ports.access_audit import AuditUnavailable
from ..gcp.access_audit import GcpAccessAuditAdapter

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _exact_service_origin(
    value: str,
    *,
    service: str,
    allow_loopback_http: bool,
) -> str:
    cleaned = validate_base_url(value, service=service)
    parsed = urlsplit(cleaned)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{service}: invalid origin {value!r}") from exc
    host = (parsed.hostname or "").lower()
    local_http = allow_loopback_http and parsed.scheme == "http" and host in _LOOPBACK_HOSTS
    if (
        cleaned != cleaned.lower()
        or not (parsed.scheme == "https" or local_http)
        or not _HOST.fullmatch(host)
        or ".." in host
        or (parsed.scheme == "https" and port is not None)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{service}: expected one exact lowercase HTTPS origin")
    return cleaned


class PlatformAccessAuditAdapter(GcpAccessAuditAdapter):
    """Fail closed unless agent-observability accepts the pseudonymized portal event."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._base_url = _exact_service_origin(
            settings.observability_url,
            service="agent-observability sink",
            allow_loopback_http=True,
        )
        self._token_audience = _exact_service_origin(
            settings.observability_audience,
            service="agent-observability token audience",
            allow_loopback_http=False,
        )

    @staticmethod
    def _fetch_token(audience: str) -> str:  # pragma: no cover - needs workload identity
        from google.auth.transport.requests import Request
        from google.oauth2.id_token import fetch_id_token

        return str(fetch_id_token(Request(), audience))

    @staticmethod
    def _post(
        url: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> None:
        response = httpx.post(url, json=dict(payload), headers=dict(headers), timeout=_TIMEOUT)
        if response.status_code // 100 != 2:
            raise AuditUnavailable(
                f"agent-observability access audit returned HTTP {response.status_code}"
            )

    def append(self, event: PortalAccessEvent) -> None:
        payload = to_observability_audit_event(event)
        url = f"{self._base_url}/v1/audit"
        parsed = urlsplit(self._base_url)
        try:
            headers: dict[str, str] = {}
            if parsed.scheme == "https":
                headers["authorization"] = f"Bearer {self._fetch_token(self._token_audience)}"
            self._post(url, payload, headers)
        except AuditUnavailable:
            raise
        except Exception as exc:
            raise AuditUnavailable("agent-observability access audit delivery failed") from exc
