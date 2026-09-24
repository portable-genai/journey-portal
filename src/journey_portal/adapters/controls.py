"""The runtime-control seam: what a switched-off access audit binds, and the startup proof.

**Disabled adapter.** When a deployment switches the access audit off
(``PORTAL_ACCESS_AUDIT=off``), the container binds :class:`DisabledAccessAudit` instead of the
profile's class. It satisfies the port, records nothing, and its integrity view says the audit is
off rather than reporting an empty ledger as a verified one. The container logs the posture at
startup.

**Startup proof.** With the audit on, the portal forwards nothing it could not audit, so it must
not report ready before it has shown the sink works: :func:`prove_access_audit` appends one
content-free probe event and, where the ledger is verifiable in process, verifies it. The health
routes answer 503 until it succeeds, which is what the Cloud Run startup probe and
``make demo-warm`` read.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from ..config import Settings
from ..domain.models import PortalAccessEvent, PortalAccessRecord, PortalAuditView
from ..ports.access_audit import AccessAuditPort, AuditUnavailable

#: What the integrity view says when there is nothing to verify because nothing is recorded.
AUDIT_OFF_ACTION = (
    "The portal access audit is switched off (PORTAL_ACCESS_AUDIT=off); no access is recorded."
)


class DisabledAccessAudit:
    """AccessAuditPort with the audit switched off: records nothing, and says so."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def append(self, event: PortalAccessEvent) -> PortalAccessRecord | None:
        return None

    def reference(self, kind: str, value: str) -> str:
        return f"{kind}:audit-off"

    @property
    def pseudonym_key_id(self) -> str:
        return "audit-off"

    def integrity(self) -> PortalAuditView:
        return PortalAuditView(
            valid=True,
            record_count=0,
            head_hash="",
            suggested_actions=(AUDIT_OFF_ACTION,),
        )

    def records(self) -> tuple[PortalAccessRecord, ...]:
        return ()


def prove_access_audit(access_audit: AccessAuditPort, *, verify_locally: bool) -> None:
    """Append one probe event, and verify the ledger where it can be verified here.

    Raises :class:`AuditUnavailable` when the sink refuses the event or the ledger does not
    verify. The event is content-free like every other: a random id, the time, and a fixed
    action, with the actor and tenant pseudonymised by the adapter itself.
    """
    access_audit.append(
        PortalAccessEvent(
            event_id=secrets.token_hex(16),
            occurred_at=datetime.now(UTC).isoformat(),
            actor_ref=access_audit.reference("actor", "portal-startup-probe"),
            tenant_ref=access_audit.reference("tenant", "portal"),
            pseudonym_key_id=access_audit.pseudonym_key_id,
            method="PROBE",
            action="startup-probe",
            app_id="portal",
        )
    )
    if verify_locally:
        view = access_audit.integrity()
        if not view.valid:
            raise AuditUnavailable("the access ledger did not verify after the startup probe")
