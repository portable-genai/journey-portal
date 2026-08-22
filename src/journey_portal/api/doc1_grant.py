"""The Doc1 Mode 5 grant-initiating routes: CSRF issue, then the brokered grant.

This is the portal half of the cross-origin embedded grant. The browser never sees a credential:
it asks the BFF for a launch code, and the BFF is the only thing that holds the service identity,
mints the ``private_key_jwt`` assertion, and speaks to Doc1's broker.

Order is the security property. Everything that can reject a forged request happens BEFORE any
credential is minted and before the broker is called, so a cross-site request never consumes a
JTI, never reaches Doc1 and never appears in Doc1's rate-limit or replay state:

1. resolve the VERIFIED principal (the identity port; a browser-asserted actor is discarded);
2. exact-``Origin`` and Fetch Metadata checks against the portal's reviewed public origin;
3. verify the session-bound CSRF token for this exact method and path;
4. only then derive the session binding, mint a fresh user-intent id, obtain the subject token,
   mint the assertion, and call the broker.

Doc1 re-validates all of it. The duplication is deliberate: a proof only the far side checks is
a proof the near side can be tricked into signing.
"""

# NOTE: deliberately no ``from __future__ import annotations`` here. The routes are defined
# inside a factory, so their ``Depends(...)`` markers reference CLOSURE variables. With postponed
# evaluation FastAPI resolves annotations against module globals only, silently fails to find the
# closure name, and demotes the dependency to a QUERY PARAMETER: every request then answers 422
# instead of running the route. Real (eagerly evaluated) annotations keep the closure in scope.
import json
import secrets
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from hex_service_kit.identity import Principal
from pydantic import BaseModel, ConfigDict, Field

from ..config import Container
from ..domain.bff_assertion import (
    AssertionPolicyError,
    ClientAssertionPolicy,
    mint_client_assertion,
)
from ..domain.csrf import (
    CSRF_HEADER,
    TOKEN_TTL_SECONDS,
    CsrfError,
    mint_csrf_token,
    session_binding,
    verify_csrf_token,
)
from ..domain.doc1_broker import (
    BrokerPolicyError,
    HostProofRejected,
    assess_browser_provenance,
    build_grant_request,
    build_host_proof,
)
from ..ports.bff_credentials import SigningKeyUnavailable
from ..ports.subject_token import SubjectTokenUnavailable
from ..ports.upstream import UpstreamClientPort

GRANT_PATH = "/v1/doc1/embed/grant"
CSRF_PATH = "/v1/doc1/embed/csrf"
#: Doc1 answers a grant in well under a second; a longer body than this is not one.
_MAX_BROKER_BODY = 8192


class CsrfTokenResponse(BaseModel):
    """One short-lived token, for the grant route only. Keep it in memory, never in storage."""

    model_config = ConfigDict(extra="forbid")

    csrf_token: str
    header: str
    method: str
    path: str
    expires_in: int


class GrantRequestModel(BaseModel):
    """What the embedded loader supplies: the instance it registered with Doc1, and nothing else.

    Every other field of the broker request comes from reviewed policy or from the portal's own
    verified session. A client that could name the client id, the scopes or the proof would be
    naming its own authorization.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(min_length=22, max_length=256)


class GrantResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    launch_code: str
    state: str
    expires_at: int


class _NoStoreRoute(APIRoute):
    """Never cache a route that carries a CSRF token or a launch code."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["Pragma"] = "no-cache"
            return response

        return handler


def create_doc1_grant_router(
    container_provider: Callable[[], Container],
    principal_resolver: Callable[[Request], Principal],
    *,
    upstream_provider: Callable[[], UpstreamClientPort],
    audit_recorder: Callable[[Request, Principal, str], Awaitable[None]],
) -> APIRouter:
    """Build the grant router with no module-level configuration or hidden global state.

    ``upstream_provider`` is taken as a FastAPI dependency rather than read off the container, so
    the offline suite can substitute a recording transport and assert that a refused request made
    NO outbound call. Reading the container directly would make that assertion impossible to
    write, and "did it refuse before or after calling the broker" is the whole point.
    """
    router = APIRouter(tags=["doc1-embed"], route_class=_NoStoreRoute)

    @router.get(CSRF_PATH, response_model=CsrfTokenResponse)
    def issue_csrf(request: Request) -> CsrfTokenResponse:
        principal = principal_resolver(request)
        settings = container_provider().settings
        secret = _session_secret(settings.session_signing_key)
        try:
            binding = session_binding(secret, subject=principal.subject, tenant=principal.tenant)
            token = mint_csrf_token(
                secret,
                binding=binding,
                method="POST",
                path=GRANT_PATH,
                issued_at=int(datetime.now(UTC).timestamp()),
                nonce=secrets.token_urlsafe(24),
            )
        except CsrfError as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return CsrfTokenResponse(
            csrf_token=token,
            header=CSRF_HEADER,
            method="POST",
            path=GRANT_PATH,
            expires_in=TOKEN_TTL_SECONDS,
        )

    @router.post(GRANT_PATH, response_model=GrantResponseModel)
    async def issue_grant(
        body: GrantRequestModel,
        request: Request,
        upstream: Annotated[UpstreamClientPort, Depends(upstream_provider)],
    ) -> Any:
        principal = principal_resolver(request)
        container = container_provider()
        settings = container.settings
        try:
            policy = settings.doc1_broker_policy
        except BrokerPolicyError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"the Doc1 brokered grant is not configured: {exc}",
            ) from exc

        # --- refuse a forged request before any credential exists -----------------
        try:
            assess_browser_provenance(
                policy,
                origin=request.headers.get("origin", ""),
                fetch_site=request.headers.get("sec-fetch-site", ""),
                fetch_mode=request.headers.get("sec-fetch-mode", ""),
                fetch_dest=request.headers.get("sec-fetch-dest", ""),
            )
        except HostProofRejected as exc:
            await audit_recorder(request, principal, "doc1-grant:denied-provenance")
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

        secret = _session_secret(settings.session_signing_key)
        try:
            binding = session_binding(secret, subject=principal.subject, tenant=principal.tenant)
            verify_csrf_token(
                request.headers.get(CSRF_HEADER, ""),
                secret,
                binding=binding,
                method="POST",
                path=GRANT_PATH,
                now=int(datetime.now(UTC).timestamp()),
            )
        except CsrfError as exc:
            await audit_recorder(request, principal, "doc1-grant:denied-csrf")
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

        # --- the request is authorized: now build the evidence and the credential --
        try:
            proof = build_host_proof(
                policy,
                subject=principal.subject,
                binding=binding,
                user_intent_id=secrets.token_urlsafe(24),
            )
            subject_token = container.subject_token.subject_token(
                subject=principal.subject, tenant=principal.tenant
            )
            assertion = mint_client_assertion(
                ClientAssertionPolicy(
                    client_id=policy.bff_client_id,
                    audience=policy.grant_endpoint,
                ),
                container.bff_signing_key,
                now=datetime.now(UTC),
                jti=secrets.token_urlsafe(24),
            )
            payload = build_grant_request(
                policy,
                instance_id=body.instance_id,
                subject_token=subject_token,
                client_assertion=assertion.value,
                proof=proof,
            )
        except HostProofRejected as exc:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except (SubjectTokenUnavailable, SigningKeyUnavailable) as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except NotImplementedError as exc:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc
        except (AssertionPolicyError, BrokerPolicyError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        await audit_recorder(request, principal, "doc1-grant:requested")
        broker = await upstream.forward(
            method="POST",
            url=policy.grant_endpoint,
            headers={"content-type": "application/json", "accept": "application/json"},
            content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )
        return _broker_response(broker.status, broker.body)

    return router


def _session_secret(configured: str) -> bytes:
    if not configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PORTAL_SESSION_SIGNING_KEY is not set, so the portal cannot bind a CSRF token "
                "or a session to this request and must not seek a brokered grant"
            ),
        )
    return configured.encode("utf-8")


def _broker_response(status_code: int, body: bytes) -> Response:
    """Relay the broker's answer without echoing an unbounded or unexpected body.

    A non-2xx broker answer is reported as a bounded, fixed message: Doc1's own error bodies are
    deliberately opaque, and re-emitting them would make the portal a probe for the broker's
    internal state.
    """
    if status_code != status.HTTP_200_OK or len(body) > _MAX_BROKER_BODY:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="the Doc1 broker rejected or could not answer the grant request",
        )
    try:
        document = json.loads(body)
        model = GrantResponseModel.model_validate(document)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="the Doc1 broker returned an unrecognized grant response",
        ) from exc
    return JSONResponse(content=model.model_dump())


__all__ = [
    "CSRF_PATH",
    "GRANT_PATH",
    "CsrfTokenResponse",
    "GrantRequestModel",
    "GrantResponseModel",
    "create_doc1_grant_router",
]
