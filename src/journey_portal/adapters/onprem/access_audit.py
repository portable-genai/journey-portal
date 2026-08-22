"""On-prem access-audit placeholder that fails until the adopter binds its WORM sink."""

from __future__ import annotations

from ...config import Settings
from ...domain.models import PortalAccessEvent, PortalAccessRecord, PortalAuditView
from ...ports.access_audit import AuditUnavailable


class OnPremAccessAuditAdapter:
    """Satisfy the port while refusing to claim an unconfigured on-prem audit trail."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def pseudonym_key_id(self) -> str:
        raise AuditUnavailable(
            "on-prem access pseudonymization requires the institution's evidence adapter"
        )

    def reference(self, kind: str, value: str) -> str:
        raise AuditUnavailable(
            "on-prem access pseudonymization requires the institution's evidence adapter"
        )

    def append(self, event: PortalAccessEvent) -> None:
        raise AuditUnavailable(
            "on-prem access audit is a portability placeholder: bind the institution's "
            "append-only evidence sink before forwarding portal traffic"
        )

    def records(self) -> tuple[PortalAccessRecord, ...]:
        raise AuditUnavailable(
            "on-prem access audit verification requires the institution's evidence adapter"
        )

    def integrity(self) -> PortalAuditView:
        raise AuditUnavailable(
            "on-prem access audit verification requires the institution's evidence adapter"
        )
