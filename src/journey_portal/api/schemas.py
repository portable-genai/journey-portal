"""Response/request models for the portal BFF's own (non-proxied) endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..domain.catalog import JourneyCatalog
from ..domain.identity_injection import persona_id
from ..domain.models import AppMount, Journey, PortalAuditView, TenantEmbedAssessment


class AppModel(BaseModel):
    """One embedded app as the shells consume it (labels + same-origin mount bases)."""

    id: str
    label: str
    mount_path: str
    ui_base: str
    api_base: str

    @classmethod
    def from_domain(cls, mount: AppMount) -> AppModel:
        return cls(
            id=mount.app_id,
            label=mount.label,
            mount_path=mount.mount_path,
            ui_base=f"{mount.mount_path}/",
            api_base=mount.artifact_api_mount_path,
        )


class JourneyModel(BaseModel):
    """One journey: its persona-scoped, ordered set of embedded apps."""

    key: str
    label: str
    blurb: str
    apps: list[AppModel]

    @classmethod
    def from_domain(cls, journey: Journey, catalog: JourneyCatalog) -> JourneyModel:
        return cls(
            key=journey.key,
            label=journey.label,
            blurb=journey.blurb,
            apps=[AppModel.from_domain(catalog.app(a)) for a in journey.app_ids],
        )


class JourneysResponse(BaseModel):
    """The whole journey catalog the shells render their nav and iframes from."""

    journeys: list[JourneyModel]


class PersonaModel(BaseModel):
    id: str
    subject: str
    tenant: str
    principals: str


class WhoAmIModel(BaseModel):
    """The portal-verified principal (the identity injected into every embedded app)."""

    subject: str
    tenant: str
    principals: list[str]
    source: str
    persona: str

    @classmethod
    def from_principal(cls, principal: object) -> WhoAmIModel:
        # ``principal`` is a hex_service_kit Principal; typed as object to avoid importing the
        # commons into this schema module (kept framework-model-only).
        from hex_service_kit.identity import Principal

        assert isinstance(principal, Principal)
        return cls(
            subject=principal.subject,
            tenant=principal.tenant,
            principals=list(principal.principals),
            source=principal.source,
            persona=persona_id(principal),
        )


class SetPersonaRequest(BaseModel):
    """Select the active demo persona (local profile only). Empty id clears to the default."""

    id: str = ""


class HealthResponse(BaseModel):
    status: str
    profile: str
    region: str


class JwkSetResponse(BaseModel):
    """The portal's published public signing keys (RFC 7517 JWK Set).

    Only public members ever appear: the signing-key port refuses to construct a published key
    that carries ``d``, ``p``, ``q``, ``dp``, ``dq``, ``qi`` or ``k``, so this response cannot
    become a private-key leak by way of a mis-shaped adapter.
    """

    keys: list[dict[str, Any]]


class AuditIntegrityFindingModel(BaseModel):
    finding_id: str
    severity: str
    summary: str
    detail: str
    evidence_id: str


class AuditIntegrityResponse(BaseModel):
    """Reviewer-facing integrity result for the local hash-chained access ledger."""

    valid: bool
    record_count: int
    head_hash: str
    escalates: bool
    findings: list[AuditIntegrityFindingModel]
    suggested_actions: list[str]

    @classmethod
    def from_domain(cls, view: PortalAuditView) -> AuditIntegrityResponse:
        return cls.model_validate(view.to_jsonable())


class EmbedPolicyFindingModel(BaseModel):
    finding_id: str
    kind: str
    severity: str
    summary: str
    detail: str
    evidence_id: str


class TenantEmbedPolicyResponse(BaseModel):
    """Reviewer-facing tenant host, framing and CORS decision."""

    request_host: str
    request_origin: str
    tenant: str
    policy_id: str
    frame_ancestors: list[str]
    cors_allowed: bool
    decision: str
    escalates: bool
    findings: list[EmbedPolicyFindingModel]
    suggested_actions: list[str]

    @classmethod
    def from_domain(cls, view: TenantEmbedAssessment) -> TenantEmbedPolicyResponse:
        return cls.model_validate(view.to_jsonable())
