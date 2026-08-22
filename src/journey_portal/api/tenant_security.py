"""Tenant-bound framing, CORS and host enforcement at the HTTP boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from hex_service_kit.identity import Principal

from ..domain.embed_policy import TenantEmbedPolicyService
from ..domain.models import TenantEmbedAssessment

_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD")
# ``x-csrf-token`` is deliberately NOT here. The grant route it belongs to refuses anything whose
# Fetch Metadata is not ``same-origin``, so a cross-origin caller could never use the header even
# if the preflight allowed it; listing it would widen the CORS surface for zero reachable benefit.
_BASE_HEADERS = frozenset({"authorization", "content-type"})
_LOCAL_HEADERS = frozenset({"x-dev-persona"})
#: The only paths served without a verified principal and without a tenant policy decision.
#: ``/healthz`` carries no tenant data; the JWK set is public key material a relying party must
#: fetch BEFORE it holds any credential of ours, so requiring a session there would make the
#: registration it exists for impossible. Both still receive the framing and nosniff headers.
UNAUTHENTICATED_PATHS = frozenset({"/healthz", "/.well-known/doc1-bff-jwks.json"})


def _error_response(exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def _append_vary(response: Response, value: str) -> None:
    current = {item.strip() for item in response.headers.get("vary", "").split(",") if item.strip()}
    current.add(value)
    response.headers["Vary"] = ", ".join(sorted(current))


def _apply_headers(
    response: Response,
    *,
    assessment: TenantEmbedAssessment | None,
    profile: str,
) -> None:
    ancestors = assessment.frame_ancestors if assessment is not None else ("'none'",)
    response.headers["Content-Security-Policy"] = f"frame-ancestors {' '.join(ancestors)}"
    if ancestors == ("'self'",):
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    elif "x-frame-options" in response.headers:
        del response.headers["x-frame-options"]
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if profile != "local":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if assessment is not None and assessment.cors_allowed:
        response.headers["Access-Control-Allow-Origin"] = assessment.request_origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        _append_vary(response, "Origin")


def add_tenant_security(
    app: FastAPI,
    *,
    profile: str,
    principal_resolver: Callable[[Request], Principal],
    policy_provider: Callable[[], TenantEmbedPolicyService],
    audit_recorder: Callable[
        [Request, Principal, TenantEmbedAssessment, str],
        Awaitable[None],
    ],
) -> None:
    """Register fail-closed host/tenant/framing/CORS enforcement and evidence."""

    @app.middleware("http")
    async def tenant_security(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path in UNAUTHENTICATED_PATHS:
            response = await call_next(request)
            _apply_headers(response, assessment=None, profile=profile)
            return response

        try:
            principal = principal_resolver(request)
            assessment = policy_provider().assess(
                request_host=request.url.hostname or "",
                tenant=principal.tenant,
                request_origin=request.headers.get("origin", ""),
            )
            request.state.embed_policy_assessment = assessment
        except HTTPException as exc:
            response = _error_response(exc)
            _apply_headers(
                response,
                assessment=getattr(request.state, "embed_policy_assessment", None),
                profile=profile,
            )
            return response

        if assessment.escalates:
            try:
                await audit_recorder(request, principal, assessment, "denied")
            except HTTPException as exc:
                response = _error_response(exc)
                _apply_headers(response, assessment=assessment, profile=profile)
                return response
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "tenant embedding policy denied the request"},
            )
            _apply_headers(response, assessment=assessment, profile=profile)
            return response

        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
            requested_method = request.headers["access-control-request-method"].upper()
            requested_headers = {
                item.strip().lower()
                for item in request.headers.get("access-control-request-headers", "").split(",")
                if item.strip()
            }
            allowed_headers = _BASE_HEADERS | (_LOCAL_HEADERS if profile == "local" else set())
            if requested_method not in _METHODS or not requested_headers <= allowed_headers:
                audit_decision = "denied-preflight"
                response = JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "CORS preflight requested a method or header not allowed"},
                )
            else:
                audit_decision = "allowed"
                response = Response(status_code=status.HTTP_204_NO_CONTENT)
                response.headers["Access-Control-Allow-Methods"] = ", ".join(_METHODS)
                response.headers["Access-Control-Allow-Headers"] = ", ".join(
                    sorted(allowed_headers)
                )
                response.headers["Access-Control-Max-Age"] = "600"
        else:
            audit_decision = "allowed"

        try:
            await audit_recorder(request, principal, assessment, audit_decision)
        except HTTPException as exc:
            response = _error_response(exc)
            _apply_headers(response, assessment=assessment, profile=profile)
            return response

        if not (
            request.method == "OPTIONS" and request.headers.get("access-control-request-method")
        ):
            response = await call_next(request)

        _apply_headers(response, assessment=assessment, profile=profile)
        return response
