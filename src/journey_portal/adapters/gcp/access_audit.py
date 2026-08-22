"""Managed-profile access evidence delivered synchronously to Cloud Logging."""

from __future__ import annotations

from collections.abc import Mapping

from ...config import Settings
from ...domain.audit import audit_key_id, audit_reference
from ...domain.models import PortalAccessEvent, PortalAccessRecord, PortalAuditView
from ...ports.access_audit import AuditUnavailable


class GcpAccessAuditAdapter:
    """Fail closed unless Cloud Logging acknowledges the bounded access event write."""

    def __init__(self, settings: Settings) -> None:
        key = settings.audit_hmac_key.encode()
        if len(key) < 32:
            raise AuditUnavailable(
                "PORTAL_AUDIT_HMAC_KEY must contain at least 32 bytes in managed profiles"
            )
        self._key = key
        self._key_id = audit_key_id(key)
        self._project_id: str | None = None

    @staticmethod
    def _write(payload: Mapping[str, object]) -> None:  # pragma: no cover - needs GCP
        from google.cloud import logging as cloud_logging

        client = cloud_logging.Client()
        client.logger("hrz9-portal-access").log_struct(dict(payload), severity="INFO")

    @property
    def pseudonym_key_id(self) -> str:
        return self._key_id

    def reference(self, kind: str, value: str) -> str:
        return audit_reference(self._key, kind, value)

    def append(self, event: PortalAccessEvent) -> None:
        payload: dict[str, object] = {
            "action": event.action,
            "actor_ref": event.actor_ref,
            "app_id": event.app_id,
            "event_id": event.event_id,
            "method": event.method,
            "occurred_at": event.occurred_at,
            "pseudonym_key_id": event.pseudonym_key_id,
            "tenant_ref": event.tenant_ref,
        }
        try:
            self._write(payload)
        except Exception as exc:
            raise AuditUnavailable("managed access audit delivery failed") from exc

    def records(self) -> tuple[PortalAccessRecord, ...]:
        raise AuditUnavailable(
            "managed access records are retained in Cloud Logging, not the local ledger"
        )

    def integrity(self) -> PortalAuditView:
        raise AuditUnavailable(
            "managed access integrity is verified through the retained evidence pack"
        )
