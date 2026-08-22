"""The hexagon boundary: every external edge the portal reaches is a ``typing.Protocol`` here.

Identity is served by the shared commons port (:class:`hex_service_kit.identity.IdentityPort`);
the portal adds one outbound-HTTP port for the reverse proxy. Enumerated by NAME so this
``__all__`` and the parity contract test together are the source of truth for what exists.
"""

from __future__ import annotations

from .access_audit import (
    AccessAuditPort,
    AuditIntegrityViolation,
    AuditUnavailable,
)
from .bff_credentials import (
    BffSigningKeyPort,
    PublishedSigningKey,
    SigningKeyUnavailable,
)
from .subject_token import SubjectTokenPort, SubjectTokenUnavailable
from .upstream import UpstreamClientPort

__all__ = [
    "AccessAuditPort",
    "AuditIntegrityViolation",
    "AuditUnavailable",
    "BffSigningKeyPort",
    "PublishedSigningKey",
    "SigningKeyUnavailable",
    "SubjectTokenPort",
    "SubjectTokenUnavailable",
    "UpstreamClientPort",
]
