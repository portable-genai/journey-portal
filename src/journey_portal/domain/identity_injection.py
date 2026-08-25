"""The identity trust boundary: how headers are rewritten as a request crosses into an app.

This is the security-critical core of the portal. The browser talks to ONE origin (the portal)
and the portal talks to each embedded app. An embedded app must see the identity the portal
verified, and must NEVER see an identity a browser asserted. So on every proxied API request the
portal:

1. STRIPS the client-spoofable identity headers from the inbound request (a browser could set
   ``X-Dev-Persona``, an IAP assertion, or an ``Authorization`` bearer; none may pass through);
2. INJECTS the identity derived from the portal-verified :class:`Principal`:
   - ``local`` profile: the app's local ``IdentityPort`` reads ``X-Dev-Persona``, so inject the
     resolved persona id. Every embedded app seeds the shared-commons personas, so the id always
     resolves there. (Offline demo/test: no IdP at all.)
   - secure profile (``gcp``/``platform``): the portal sits behind Identity-Aware Proxy, so the
     edge-injected ``x-goog-iap-jwt-assertion`` on the inbound request is trustworthy; forward
     exactly that value (having stripped any prior copy first). Each app re-verifies it itself.

**The rules themselves now live in ``hex_service_kit.federation``, and this module re-exports
them.** They were only ever implemented here, while fifty-four repositories carried their own
copy of the RECEIVING half and had already drifted apart on it. A trust boundary described in
two places is a trust boundary that will be described differently in two places, so the
population adopts one reviewed implementation and this module is the portal's view of it.

What stays here is the portal's own vocabulary: its profile names and the ``InjectionPlan``
type its proxy adapter passes around. What moved is every security decision -- which headers a
browser may never assert, which credential a proxy must not forward, and the two names one
assertion travels under.
"""

from __future__ import annotations

from collections.abc import Mapping

from hex_service_kit.federation import (
    CLIENT_SPOOFABLE_IDENTITY,
    HOP_BY_HOP_REQUEST,
    IAP_ASSERTION_HEADER,
    PERSONA_HEADER,
    PORTAL_ASSERTION_HEADER,
)
from hex_service_kit.federation import (
    build_injection_plan as _kit_build_injection_plan,
)
from hex_service_kit.federation import (
    persona_id as persona_id,
)
from hex_service_kit.federation import (
    sanitize_response_headers as sanitize_response_headers,
)
from hex_service_kit.identity import Principal

from .models import InjectionPlan

# The portal's own names for the kit's headers, kept so this repository's call sites and tests
# read as they always have. They are aliases, not copies: rebinding them here means a drift
# between the portal and the kit is impossible rather than merely unlikely.
IAP_HEADER = IAP_ASSERTION_HEADER
PERSONA_HEADER = PERSONA_HEADER
PORTAL_IAP_HEADER = PORTAL_ASSERTION_HEADER

LOCAL_PROFILE = "local"
SECURE_PROFILES: tuple[str, ...] = ("gcp", "platform")

__all__ = [
    "CLIENT_SPOOFABLE_IDENTITY",
    "IAP_HEADER",
    "LOCAL_PROFILE",
    "PERSONA_HEADER",
    "PORTAL_IAP_HEADER",
    "SECURE_PROFILES",
    "build_injection_plan",
    "persona_id",
    "sanitize_request_headers",
    "sanitize_response_headers",
]


def build_injection_plan(
    principal: Principal,
    profile: str,
    inbound: Mapping[str, str],
) -> InjectionPlan:
    """Build the header-rewrite plan for a request crossing into an embedded app's backend.

    ``inbound`` keys are lower-cased. In every profile the client-spoofable identity headers are
    stripped; the injected identity depends on the profile (see the module docstring). ``onprem``
    injects nothing (its proxy adapter is a fail-fast placeholder).

    Delegates the decision to the kit and re-wraps the result in this repository's own
    :class:`InjectionPlan`, which its proxy adapter and eval gate are written against.
    """
    plan = _kit_build_injection_plan(
        principal,
        profile,
        inbound,
        local_profile=LOCAL_PROFILE,
        secure_profiles=SECURE_PROFILES,
    )
    return InjectionPlan(set_headers=plan.set_headers, strip_headers=plan.strip_headers)


def sanitize_request_headers(inbound: Mapping[str, str], plan: InjectionPlan) -> dict[str, str]:
    """Apply ``plan`` to inbound headers: drop hop-by-hop + stripped identity, then inject.

    The result is the exact header set the portal forwards to the upstream backend. Injected
    (``set``) headers are applied last so they always win over anything that leaked through.
    """
    out: dict[str, str] = {}
    for key, value in inbound.items():
        lower = key.lower()
        if lower in HOP_BY_HOP_REQUEST or lower in plan.strip_headers:
            continue
        out[lower] = value
    for key, value in plan.set_headers:
        out[key.lower()] = value
    return out
