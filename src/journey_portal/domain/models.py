"""Pure value objects for the journey portal (stdlib only, no framework/cloud imports).

The portal is a host shell: a :class:`Journey` is a persona-scoped ordered set of embedded
:class:`AppMount` s. Compatibility entries live under ``/apps/<app_id>`` while a portable artifact
may declare a fixed canonical mount such as cdd-sow-research's ``/agent``. :class:`UpstreamResponse`
is the framework-free result of a reverse-proxied call, and :class:`InjectionPlan` describes how
identity headers are rewritten as a request crosses into an embedded app.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

_MOUNT_ROOT = "/apps"


@dataclass(frozen=True, slots=True)
class AppMount:
    """A built catalog app mounted same-origin under the portal.

    ``ui_upstream`` is the app's basePath-aware Next.js UI; ``api_upstream`` is its FastAPI
    backend, which serves ``/v1/...`` at its own root. ``canonical_mount_path`` lets an artifact
    keep one path across hosts while ``mount_path`` remains its portal compatibility entry.
    """

    app_id: str
    label: str
    ui_upstream: str
    api_upstream: str
    canonical_mount_path: str | None = None

    @property
    def mount_path(self) -> str:
        return f"{_MOUNT_ROOT}/{self.app_id}"

    @property
    def api_mount_path(self) -> str:
        return f"{_MOUNT_ROOT}/{self.app_id}/api"

    @property
    def artifact_mount_path(self) -> str:
        """The path emitted by the app artifact; defaults to the compatibility mount."""
        return self.canonical_mount_path or self.mount_path

    @property
    def artifact_api_mount_path(self) -> str:
        return f"{self.artifact_mount_path}/api"


@dataclass(frozen=True, slots=True)
class Journey:
    """A persona-scoped, ordered set of embedded apps composed into one UI."""

    key: str
    label: str
    blurb: str
    app_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    """The framework-free result of a reverse-proxied upstream call."""

    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class InjectionPlan:
    """How identity headers are rewritten on a request crossing into an embedded app.

    ``strip_headers`` are lower-cased header names removed from the inbound request (the
    client-spoofable identity headers, always discarded); ``set_headers`` are the
    portal-resolved identity headers injected afterwards. Strip precedes set, so a browser can
    never assert an identity to an upstream: only the portal-verified principal reaches it.
    """

    set_headers: tuple[tuple[str, str], ...] = ()
    strip_headers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PortalAccessEvent:
    """Content-free metadata for one request forwarded through the portal."""

    event_id: str
    occurred_at: str
    actor_ref: str
    tenant_ref: str
    pseudonym_key_id: str
    method: str
    action: str
    app_id: str


@dataclass(frozen=True, slots=True)
class PortalAccessRecord:
    """One append-only access event bound to the preceding record by SHA-256."""

    sequence: int
    event: PortalAccessEvent
    previous_hash: str
    record_hash: str


class AuditSeverity(StrEnum):
    """Severity of a deterministic audit-integrity finding."""

    HIGH = "high"


@dataclass(frozen=True, slots=True)
class AuditIntegrityFinding:
    """A stable, evidence-linked explanation of one ledger-integrity failure."""

    finding_id: str
    severity: AuditSeverity
    summary: str
    detail: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class PortalAuditView:
    """Audit-first output for a reviewer verifying the local access ledger."""

    valid: bool
    record_count: int
    head_hash: str
    findings: tuple[AuditIntegrityFinding, ...] = ()
    suggested_actions: tuple[str, ...] = ()

    @property
    def escalates(self) -> bool:
        """Any integrity finding requires human investigation."""
        return bool(self.findings)

    def to_jsonable(self) -> dict[str, object]:
        """Return a dependency-free JSON-ready representation."""
        return {
            "valid": self.valid,
            "record_count": self.record_count,
            "head_hash": self.head_hash,
            "escalates": self.escalates,
            "findings": [
                {
                    "finding_id": finding.finding_id,
                    "severity": finding.severity.value,
                    "summary": finding.summary,
                    "detail": finding.detail,
                    "evidence_id": finding.evidence_id,
                }
                for finding in self.findings
            ],
            "suggested_actions": list(self.suggested_actions),
        }


class EmbedPolicySeverity(StrEnum):
    """Severity of a tenant embedding-policy finding."""

    HIGH = "high"


class EmbedPolicyFindingKind(StrEnum):
    """Stable finding kinds emitted by the tenant embedding-policy service."""

    AMBIGUOUS_HOST = "ambiguous-host"
    ORIGIN_DENIED = "origin-denied"
    TENANT_HOST_MISMATCH = "tenant-host-mismatch"
    UNKNOWN_HOST = "unknown-host"


@dataclass(frozen=True, slots=True)
class TenantEmbedPolicy:
    """Reviewed host, framing and CORS policy for one tenant deployment boundary."""

    policy_id: str
    tenant: str
    hosts: tuple[str, ...]
    frame_ancestors: tuple[str, ...]
    cors_origins: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EmbedPolicyFinding:
    """Evidence-linked explanation of a denied tenant embedding request."""

    finding_id: str
    kind: EmbedPolicyFindingKind
    severity: EmbedPolicySeverity
    summary: str
    detail: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class TenantEmbedAssessment:
    """Audit-first result of resolving a request against the tenant policy registry."""

    request_host: str
    request_origin: str
    tenant: str
    policy_id: str
    frame_ancestors: tuple[str, ...]
    cors_allowed: bool
    findings: tuple[EmbedPolicyFinding, ...] = ()
    suggested_actions: tuple[str, ...] = ()

    @property
    def escalates(self) -> bool:
        """Any policy mismatch is a security review event."""
        return bool(self.findings)

    @property
    def decision(self) -> str:
        """The enforcement adapter fails closed on every escalated assessment."""
        return "denied" if self.escalates else "allowed"

    def to_jsonable(self) -> dict[str, object]:
        """Return a dependency-free JSON-ready reviewer view."""
        return {
            "request_host": self.request_host,
            "request_origin": self.request_origin,
            "tenant": self.tenant,
            "policy_id": self.policy_id,
            "frame_ancestors": list(self.frame_ancestors),
            "cors_allowed": self.cors_allowed,
            "decision": self.decision,
            "escalates": self.escalates,
            "findings": [
                {
                    "finding_id": finding.finding_id,
                    "kind": finding.kind.value,
                    "severity": finding.severity.value,
                    "summary": finding.summary,
                    "detail": finding.detail,
                    "evidence_id": finding.evidence_id,
                }
                for finding in self.findings
            ],
            "suggested_actions": list(self.suggested_actions),
        }
