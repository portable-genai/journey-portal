"""Port for append-only, content-free portal access evidence."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import PortalAccessEvent, PortalAccessRecord, PortalAuditView


class AuditUnavailable(RuntimeError):
    """The configured audit sink cannot durably accept or verify an event."""


class AuditIntegrityViolation(AuditUnavailable):
    """Existing audit evidence does not match its retained integrity checkpoint."""


@runtime_checkable
class AccessAuditPort(Protocol):
    """Persist access metadata and expose records when local verification is supported."""

    def append(self, event: PortalAccessEvent) -> PortalAccessRecord | None:
        """Write one access event before the request crosses the portal boundary."""
        ...

    def reference(self, kind: str, value: str) -> str:
        """Create a deployment-scoped pseudonymous reference outside the domain record."""
        ...

    @property
    def pseudonym_key_id(self) -> str:
        """Identify the active pseudonym key without exposing it."""
        ...

    def integrity(self) -> PortalAuditView:
        """Verify the ledger against its separately retained checkpoint."""
        ...

    def records(self) -> tuple[PortalAccessRecord, ...]:
        """Return the ordered ledger for deterministic local verification."""
        ...
