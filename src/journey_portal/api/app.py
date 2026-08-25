"""FastAPI application for the Hrz9 Journey Portal Shell (the BFF).

Import-safe (the Container is built at request time, never at import). The portal is a same-origin
reverse proxy in front of the built P1 apps: most use ``/apps/<id>/*`` while fixed portable
artifact mounts such as Doc1's ``/agent/*`` retain ``/apps/<id>`` compatibility entries. On every
proxied API request it rewrites identity so the embedded app sees the portal-verified
:class:`Principal` and never a browser-asserted one
(``journey_portal.domain.identity_injection``). Its own endpoints feed the shells the journey
catalog, the persona list, and the current identity. Fail-closed network defaults come from the
commons (loopback bind for the no-auth local profile; CORS never falls back to ``*``).
"""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from hex_service_kit.identity import Principal, RequestContext
from hex_service_kit.netdefaults import resolve_bind_host
from hex_service_kit.web import add_loopback_exposure_guard
from starlette.concurrency import run_in_threadpool

from ..config import (
    Container,
    ProfileNotConfigured,
    Settings,
    build_container,
    end_user_auth_kind,
    resolve_profile,
)
from ..domain.catalog import api_target, ui_target
from ..domain.errors import UnknownApp
from ..domain.identity_injection import (
    CLIENT_SPOOFABLE_IDENTITY,
    IAP_HEADER,
    PERSONA_HEADER,
    SECURE_PROFILES,
    build_injection_plan,
    sanitize_request_headers,
    sanitize_response_headers,
)
from ..domain.models import (
    AppMount,
    InjectionPlan,
    PortalAccessEvent,
    TenantEmbedAssessment,
    UpstreamResponse,
)
from ..ports.access_audit import AuditUnavailable
from ..ports.bff_credentials import SigningKeyUnavailable
from ..ports.identity import VERIFIED
from ..ports.upstream import UpstreamClientPort
from .doc1_grant import create_doc1_grant_router
from .schemas import (
    AuditIntegrityResponse,
    HealthResponse,
    JourneyModel,
    JourneysResponse,
    JwkSetResponse,
    SetPersonaRequest,
    TenantEmbedPolicyResponse,
    WhoAmIModel,
)
from .security import make_get_principal
from .tenant_security import add_tenant_security

_LOGGER = logging.getLogger(__name__)


def _audit_unavailable(exc: AuditUnavailable) -> HTTPException:
    """Refuse the request, and say WHY somewhere an operator can read it.

    The four call sites below all failed closed with the same fixed ``detail`` and logged
    nothing, so a deployed portal that could not write its access evidence answered 503 to every
    request with no way to tell a missing IAM grant from an unreachable sink from a short HMAC
    key. The reason stays out of the RESPONSE (a caller learns only that the audit is
    unavailable) and goes to the log, which is where an operator is looking.

    It is deliberately raised from the handler rather than from the adapter: the adapter is built
    lazily inside the same call, so a construction failure never reached the adapter's own
    except block at all. That is the case this exists to make visible.
    """

    _LOGGER.error("portal access audit is unavailable: %s", exc)
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="portal access audit is unavailable",
    )


# The profile is resolved ONCE, by config.resolve_profile, and an absent PORTAL_PROFILE is NOT
# consent. This module used to re-derive it with its own os.environ.get(..., "local"), which was
# both a second source of truth and an UNVALIDATED one: a typo'd PORTAL_PROFILE was rejected by
# Settings.load but still selected every local relaxation here.
_CHOICE = resolve_profile()
# Every RELAXATION below keys off this: an unconsented run is not the local profile, so it gets
# no persona selector, no injected X-Dev-Persona, no local-only ops route and HSTS on.
_EXPOSURE = _CHOICE.exposure_profile
_PERSONA_COOKIE = "portal_persona"
_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
#: Doc1 pins this portal's ``private_key_jwt`` client against the JWK set published here. The
#: path is the one recorded in Doc1's named-deployment dossier, so changing it breaks a reviewed
#: registration; treat it as part of the deployment contract, not an internal detail.
BFF_JWKS_PATH = "/.well-known/doc1-bff-jwks.json"
# On a UI (non-API) proxy hop no identity is injected, but the client-spoofable identity headers
# are still stripped so nothing a browser set leaks to the app's origin.
_UI_STRIP_PLAN = InjectionPlan(strip_headers=CLIENT_SPOOFABLE_IDENTITY)


@lru_cache(maxsize=1)
def _container() -> Container:
    """The process-wide container, turning ANY misconfiguration into a legible 503.

    Two failure shapes reach here, and both must be a named 503, never a bare 500:

    * With PORTAL_PROFILE unset there is no reviewed tenant embed registry (the seeded wildcard
      one is a relaxation nobody consented to) and the domain requires at least one policy, so the
      portal cannot serve at all. That is the correct answer for a reverse proxy fronting every
      embedded app, and a 503 naming the variable is a better one than an unhandled ValueError.
    * Every OTHER configuration error ``Settings.load`` raises (a region outside the allowlist, a
      non-numeric upstream timeout, a malformed tenant-embed JSON, an emptied scope list, a
      platform profile missing its observability endpoint) is a ``ValueError`` too, and used to
      escape as a bare 500 because only :class:`ProfileNotConfigured` was caught. The journey
      catalog is validated here as well (``list_journeys``), so a missing or malformed
      ``config/journeys.yaml`` is a named 503 rather than a 500 surfaced later from a proxy hop;
      the adapters stay lazy, so no cloud SDK is imported on this path.

    The profile string itself is validated at IMPORT (``_CHOICE = resolve_profile()`` at module
    scope), so a bad or empty PORTAL_PROFILE is a boot failure and never reaches this request-time
    refusal. ``lru_cache`` never caches a raised exception, so a refusal is re-derived every
    request and fixing the configuration recovers without a restart.
    """
    try:
        container = build_container(Settings.load())
        container.catalog.list_journeys()
        return container
    except ProfileNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"portal configuration error: {exc}",
        ) from exc


def _upstream() -> UpstreamClientPort:
    """The reverse-proxy client (a FastAPI dependency, overridden by a fake in offline tests)."""
    return _container().upstream


def _resolve_app(app_id: str) -> AppMount:
    try:
        return _container().catalog.app(app_id)
    except UnknownApp as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown app {app_id!r}") from exc


async def _record_forwarded_access(
    *,
    mount: AppMount,
    request: Request,
    principal: Principal,
    channel: str,
) -> None:
    """Fail closed unless content-free access metadata reaches the configured audit sink."""

    def append() -> None:
        access_audit = _container().access_audit
        event = PortalAccessEvent(
            event_id=secrets.token_hex(16),
            occurred_at=datetime.now(UTC).isoformat(),
            actor_ref=access_audit.reference("actor", principal.subject),
            tenant_ref=access_audit.reference("tenant", principal.tenant),
            pseudonym_key_id=access_audit.pseudonym_key_id,
            method=request.method.upper(),
            action=f"forward:{channel}",
            app_id=mount.app_id,
        )
        access_audit.append(event)

    try:
        await run_in_threadpool(append)
    except AuditUnavailable as exc:
        raise _audit_unavailable(exc) from exc


async def _record_embed_policy_assessment(
    request: Request,
    principal: Principal,
    assessment: TenantEmbedAssessment,
    audit_decision: str,
) -> None:
    """Persist one content-free allow/deny event for each enforced tenant policy decision."""

    def append() -> None:
        access_audit = _container().access_audit
        access_audit.append(
            PortalAccessEvent(
                event_id=secrets.token_hex(16),
                occurred_at=datetime.now(UTC).isoformat(),
                actor_ref=access_audit.reference("actor", principal.subject),
                tenant_ref=access_audit.reference("tenant", principal.tenant),
                pseudonym_key_id=access_audit.pseudonym_key_id,
                method=request.method.upper(),
                action=f"embed-policy:{audit_decision}",
                app_id=f"portal:{assessment.policy_id or 'unresolved'}",
            )
        )

    try:
        await run_in_threadpool(append)
    except AuditUnavailable as exc:
        raise _audit_unavailable(exc) from exc


async def _record_grant_decision(
    request: Request,
    principal: Principal,
    action: str,
) -> None:
    """Record one content-free decision on the Doc1 grant path (allowed or refused)."""

    def append() -> None:
        access_audit = _container().access_audit
        access_audit.append(
            PortalAccessEvent(
                event_id=secrets.token_hex(16),
                occurred_at=datetime.now(UTC).isoformat(),
                actor_ref=access_audit.reference("actor", principal.subject),
                tenant_ref=access_audit.reference("tenant", principal.tenant),
                pseudonym_key_id=access_audit.pseudonym_key_id,
                method=request.method.upper(),
                action=action,
                app_id="doc1",
            )
        )

    try:
        await run_in_threadpool(append)
    except AuditUnavailable as exc:
        raise _audit_unavailable(exc) from exc


get_principal = make_get_principal(lambda: _container().identity, persona_cookie=_PERSONA_COOKIE)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    yield
    await _container().upstream.aclose()


app = FastAPI(
    title="Hrz9 Journey Portal Shell",
    version="0.1.0",
    description="Persona-journey host portal: composes the built P1 app UIs into one UI per user "
    "(RM and Ops journeys) via same-origin reverse-proxy embedding, with portal-verified identity "
    "injected into every embedded app. Region asia-southeast1.",
    lifespan=_lifespan,
)
app.state.profile = _CHOICE.profile
app.state.profile_choice = _CHOICE

add_tenant_security(
    app,
    profile=_EXPOSURE,
    principal_resolver=get_principal,
    policy_provider=lambda: _container().embed_policy,
    audit_recorder=_record_embed_policy_assessment,
)

# A request arrives with nothing authenticating the END USER unless BOTH of these hold, and the
# guard bounds every case where either fails:
#
#   1. a profile was chosen. Absent that, nobody selected an identity scheme at all, and it is
#      the one case where a binding that named a verifying adapter must NOT buy the relaxation:
#      unset is not consent, whatever the binding says;
#   2. the identity adapter the active binding names DECLARES that it VERIFIES the end user. The
#      seeded personas are chosen by the caller and then INJECTED into every embedded app; the
#      on-premises placeholder resolves nobody. Neither authenticates anyone, so neither may
#      switch this off. The adapter is read from the BINDING, so an adopter who wires their own
#      IdP verifier under `onprem` lifts the bound without touching this expression.
#
# `add_tenant_security` above is NOT this control and does not substitute for it. It compares the
# request's HOST HEADER against the reviewed tenant embed policy: a value the CLIENT writes. It
# was proved bypassable exactly that way, with a peer on the LAN sending `Host: 127.0.0.1:8110`
# and being answered 200 with the full seeded principal on `/v1/whoami`. The host check bounds
# which tenant a request may be framed as; this one bounds WHERE the request may come from. Both
# stay.
_END_USER_AUTHENTICATED = _CHOICE.explicit and end_user_auth_kind(_CHOICE.profile) == VERIFIED

# The RESTRICTION's profile string. `bind_profile` already reads an unconsented run as `local`;
# this widens the same rule to every posture that cannot authenticate an end user, so the
# start-up bound in `main()` and the request-time guard agree instead of one binding every
# interface while the other refuses every peer that reaches it.
_BIND_PROFILE = _CHOICE.bind_profile if _END_USER_AUTHENTICATED else "local"

# Registered LAST, so it is the OUTERMOST middleware: an off-loopback caller is refused before
# the tenant-security middleware, before CORS and before any route or dependency runs, and its
# refusal therefore cannot be steered by the Host header that middleware reads. Bound to the APP
# OBJECT, not to `main()`: the Dockerfile CMD is
# `exec uvicorn journey_portal.api.app:app --host 0.0.0.0 --port ${PORT:-8110}`, which never
# reaches `main()`, so a guard living only there is dead in every shipped process.
add_loopback_exposure_guard(
    app,
    unauthenticated=not _END_USER_AUTHENTICATED,
    insecure_demo_env="PORTAL_ALLOW_INSECURE_DEMO",
    # The EXPOSURE profile, so a run nobody configured names itself 'unconfigured' in the
    # refusal rather than borrowing the name of a profile an operator never chose.
    posture=_EXPOSURE,
)
app.include_router(
    create_doc1_grant_router(
        _container,
        get_principal,
        upstream_provider=_upstream,
        audit_recorder=_record_grant_decision,
    )
)


def _proxied_response(resp: UpstreamResponse) -> Response:
    """Rebuild a client-facing response from an upstream one, preserving multi-valued headers.

    The framing headers (content-length / content-encoding / transfer-encoding) are dropped and
    content-length recomputed from the body the portal actually forwards, so a decoded body is
    never mis-framed. ``set-cookie`` and every other header pass through verbatim.
    """
    response = Response(content=resp.body, status_code=resp.status)
    raw: list[tuple[bytes, bytes]] = [
        (k.encode("latin-1"), v.encode("latin-1"))
        for k, v in sanitize_response_headers(resp.headers)
    ]
    raw.append((b"content-length", str(len(resp.body)).encode("latin-1")))
    response.raw_headers = raw
    return response


# --------------------------------------------------------------------------- #
# Portal-native endpoints (consumed by the RM / Ops shells)
# --------------------------------------------------------------------------- #
@app.get("/healthz", response_model=HealthResponse, tags=["ops"])
def healthz() -> HealthResponse:
    settings = _container().settings
    return HealthResponse(status="ok", profile=settings.profile, region=settings.region)


@app.get(BFF_JWKS_PATH, response_model=JwkSetResponse, tags=["ops"])
def bff_jwks(response: Response) -> JwkSetResponse:
    """Publish the BFF's public signing keys so Doc1 can pin the ``private_key_jwt`` client.

    Unauthenticated and cacheable by design: a JWK set is public key material, it is what a
    relying party fetches before it has any credential of ours, and a login wall in front of it
    would simply prevent registration. Only public members are ever emitted; the port refuses to
    construct a published key that carries a private JWK member.

    Cacheable for five minutes, which is short enough that a rotation propagates inside the
    accepted-key overlap window recorded in the dossier, and long enough that a verifier does
    not refetch per request.
    """
    try:
        keys = _container().bff_signing_key.published_keys()
    except SigningKeyUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"the portal has no publishable BFF signing key: {exc}",
        ) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    response.headers["Cache-Control"] = "public, max-age=300"
    return JwkSetResponse(keys=[dict(key.public_jwk) for key in keys])


@app.get("/v1/journeys", response_model=JourneysResponse, tags=["portal"])
def journeys() -> JourneysResponse:
    """The journey catalog the shells render their nav + iframes from."""
    catalog = _container().catalog
    return JourneysResponse(
        journeys=[JourneyModel.from_domain(j, catalog) for j in catalog.list_journeys()]
    )


@app.get("/v1/personas", tags=["portal"])
def personas() -> list[dict[str, str]]:
    """The seeded dev personas for the local persona picker (empty outside local)."""
    identity = _container().identity
    lister = getattr(identity, "personas", None)
    if lister is None:
        return []
    return [dict(p) for p in lister()]


@app.get("/v1/whoami", response_model=WhoAmIModel, tags=["portal"])
def whoami(principal: Annotated[Principal, Depends(get_principal)]) -> WhoAmIModel:
    """The portal-verified principal: exactly the identity injected into every embedded app."""
    return WhoAmIModel.from_principal(principal)


@app.get(
    "/v1/embed-policy",
    response_model=TenantEmbedPolicyResponse,
    tags=["portal"],
)
def embed_policy(request: Request) -> TenantEmbedPolicyResponse:
    """Expose the exact tenant host/framing/CORS decision applied to this response."""
    assessment = getattr(request.state, "embed_policy_assessment", None)
    if not isinstance(assessment, TenantEmbedAssessment):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tenant embedding policy assessment is unavailable",
        )
    return TenantEmbedPolicyResponse.from_domain(assessment)


@app.get(
    "/v1/audit/integrity",
    response_model=AuditIntegrityResponse,
    tags=["ops"],
)
def audit_integrity(
    principal: Annotated[Principal, Depends(get_principal)],
) -> AuditIntegrityResponse:
    """Verify the local access ledger; managed profiles use retained Cloud Logging evidence."""
    del principal
    if _EXPOSURE != "local":
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail="managed audit integrity is verified through the retained evidence pack",
        )
    try:
        view = _container().access_audit.integrity()
    except AuditUnavailable as exc:
        raise _audit_unavailable(exc) from exc
    return AuditIntegrityResponse.from_domain(view)


@app.post("/v1/session/persona", response_model=WhoAmIModel, tags=["portal"])
def set_persona(request: SetPersonaRequest, response: Response) -> WhoAmIModel:
    """Select the active demo persona (local profile only); an empty id clears to the default."""
    if _EXPOSURE != "local":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="persona selection is a local-profile demo affordance; "
            "secure profiles read the IdP",
        )
    identity = _container().identity
    lister = getattr(identity, "personas", None)
    valid = {p["id"] for p in lister()} if lister else set()
    chosen = request.id.strip()
    if chosen and chosen not in valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown persona {chosen!r}"
        )
    if chosen:
        response.set_cookie(_PERSONA_COOKIE, chosen, httponly=True, samesite="lax", path="/")
    else:
        response.delete_cookie(_PERSONA_COOKIE, path="/")
    ctx = RequestContext(headers={PERSONA_HEADER: chosen} if chosen else {})
    return WhoAmIModel.from_principal(identity.resolve(ctx))


# --------------------------------------------------------------------------- #
# The reverse proxy: /apps/<id>/api/* (identity-injected) and /apps/<id>/* (UI)
# --------------------------------------------------------------------------- #
async def _proxy_api_request(
    mount: AppMount,
    tail: str,
    request: Request,
    principal: Principal,
    upstream: UpstreamClientPort,
) -> Response:
    inbound = {k.lower(): v for k, v in request.headers.items()}
    plan = build_injection_plan(principal, _EXPOSURE, inbound)
    fwd_headers = sanitize_request_headers(inbound, plan)
    if _EXPOSURE in SECURE_PROFILES and IAP_HEADER not in fwd_headers:
        # A secure-profile API hop carrying no injected identity is not a subtle problem: the
        # embedded app re-verifies the assertion itself and will refuse, so the browser sees the
        # APP's 401 and the portal looks healthy. Say it here, where the cause is.
        _LOGGER.warning(
            "forwarding %s to %s with NO injected IAP assertion (inbound had it: %s)",
            request.method,
            mount.app_id,
            IAP_HEADER in inbound,
        )
    url = api_target(mount, tail)
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()
    await _record_forwarded_access(
        mount=mount,
        request=request,
        principal=principal,
        channel="api",
    )
    resp = await upstream.forward(method=request.method, url=url, headers=fwd_headers, content=body)
    return _proxied_response(resp)


async def _proxy_ui_request(
    mount: AppMount,
    public_path: str,
    request: Request,
    principal: Principal,
    upstream: UpstreamClientPort,
) -> Response:
    inbound = {k.lower(): v for k, v in request.headers.items()}
    fwd_headers = sanitize_request_headers(inbound, _UI_STRIP_PLAN)
    url = ui_target(mount, public_path)
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()
    await _record_forwarded_access(
        mount=mount,
        request=request,
        principal=principal,
        channel="ui",
    )
    resp = await upstream.forward(method=request.method, url=url, headers=fwd_headers, content=body)
    return _proxied_response(resp)


def _compatibility_redirect(mount: AppMount, request: Request) -> RedirectResponse | None:
    if mount.artifact_mount_path == mount.mount_path:
        return None
    suffix = request.url.path.removeprefix(mount.mount_path)
    target = f"{mount.artifact_mount_path}{suffix}"
    if not suffix:
        target = f"{target}/"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


def _doc1_mount() -> AppMount:
    mount = _resolve_app("doc1")
    if mount.artifact_mount_path != "/agent":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Doc1 canonical artifact mount is not configured",
        )
    return mount


@app.api_route("/agent/api/{tail:path}", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_doc1_canonical_api(
    tail: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
) -> Response:
    """Expose the canonical Doc1 API path and strip /agent/api before forwarding."""
    return await _proxy_api_request(_doc1_mount(), tail, request, principal, upstream)


@app.api_route("/agent/", methods=_PROXY_METHODS, include_in_schema=False)
@app.api_route("/agent", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_doc1_canonical_ui_root(
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
) -> Response:
    """Expose the fixed Doc1 artifact root without rewriting its emitted paths."""
    return await _proxy_ui_request(_doc1_mount(), "/agent", request, principal, upstream)


@app.api_route("/agent/{tail:path}", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_doc1_canonical_ui(
    tail: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
) -> Response:
    return await _proxy_ui_request(
        _doc1_mount(),
        f"/agent/{tail}",
        request,
        principal,
        upstream,
    )


@app.api_route("/apps/{app_id}/api/{tail:path}", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_api(
    app_id: str,
    tail: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
) -> Response:
    """Proxy an embedded app's backend call, injecting the portal-verified identity."""
    mount = _resolve_app(app_id)
    return await _proxy_api_request(mount, tail, request, principal, upstream)


@app.api_route("/apps/{app_id}/", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_ui_root(
    app_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
) -> Response:
    """Serve an embedded UI root without leaking its loopback redirect to the browser."""
    mount = _resolve_app(app_id)
    if redirect := _compatibility_redirect(mount, request):
        return redirect
    # Next normalizes its basePath root to no trailing slash.  Follow that internal convention
    # inside the BFF so the browser remains on the shell's same origin.
    return await _proxy_ui_request(
        mount,
        request.url.path.rstrip("/"),
        request,
        principal,
        upstream,
    )


@app.api_route("/apps/{app_id}", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_ui_base(
    app_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
) -> Response:
    """Proxy the slash-normalized iframe root before FastAPI redirects to a loopback URL."""
    mount = _resolve_app(app_id)
    if redirect := _compatibility_redirect(mount, request):
        return redirect
    return await _proxy_ui_request(mount, request.url.path, request, principal, upstream)


@app.api_route("/apps/{app_id}/{tail:path}", methods=_PROXY_METHODS, include_in_schema=False)
async def proxy_ui(
    app_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    upstream: Annotated[UpstreamClientPort, Depends(_upstream)],
    tail: str,
) -> Response:
    """Proxy an embedded app's basePath-aware UI (full path forwarded; no identity injection)."""
    mount = _resolve_app(app_id)
    if redirect := _compatibility_redirect(mount, request):
        return redirect
    return await _proxy_ui_request(mount, request.url.path, request, principal, upstream)


def main() -> None:
    """Run the BFF locally with uvicorn; the SECOND layer of the exposure bound, not the only one.

    This refuses to BIND a non-loopback interface, which is the earlier and clearer failure, but
    it runs only when the process is started through this function. The shipped entry point is
    not: the Dockerfile CMD hands ``journey_portal.api.app:app`` straight to uvicorn with
    ``--host 0.0.0.0``. The bound that always applies is the ``add_loopback_exposure_guard``
    middleware registered on the app object above; do not delete it believing this covers it.
    """
    import uvicorn

    host = resolve_bind_host(
        _BIND_PROFILE,
        host_env="PORTAL_API_HOST",
        insecure_demo_env="PORTAL_ALLOW_INSECURE_DEMO",
    )
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8110")))


if __name__ == "__main__":
    main()
