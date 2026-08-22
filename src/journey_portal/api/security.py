"""Portal identity resolution: the persona cookie is the local selector; secure reads the IdP.

The portal's persona picker is a same-origin cookie (``portal_persona``), so this resolver maps
that cookie onto the ``X-Dev-Persona`` header the shared-commons local ``IdentityPort`` reads,
then resolves a verified :class:`Principal` exactly like every other service. Secure adapters
ignore the mapped header (they read the IAP assertion), so the mapping is harmless in production.
The request-body/query actor is never read: identity flows only from here.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, status
from hex_service_kit.identity import IdentityError, IdentityPort, Principal, RequestContext

from ..domain.identity_injection import PERSONA_HEADER


def make_get_principal(
    identity_provider: Callable[[], IdentityPort],
    *,
    persona_cookie: str,
) -> Callable[[Request], Principal]:
    """Build a FastAPI dependency resolving the verified portal principal (or 401)."""

    def get_principal(request: Request) -> Principal:
        existing = getattr(request.state, "principal", None)
        if isinstance(existing, Principal):
            return existing
        headers = {k.lower(): v for k, v in request.headers.items()}
        # The browser must NOT assert identity to the portal: drop the client-spoofable persona
        # and bearer before resolving, so the portal's own selector wins. (The IAP assertion is
        # injected by the edge in secure mode, never by the browser, so it is left intact.) The
        # portal's persona selector is the cookie, mapped to the header the local adapter reads.
        headers.pop(PERSONA_HEADER, None)
        headers.pop("authorization", None)
        cookie_val = request.cookies.get(persona_cookie, "").strip()
        if cookie_val:
            headers[PERSONA_HEADER] = cookie_val
        ctx = RequestContext(headers=headers)
        try:
            principal = identity_provider().resolve(ctx)
            request.state.principal = principal
            return principal
        except IdentityError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            ) from exc

    return get_principal
