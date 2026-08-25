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

Strip precedes set, so the injected identity always wins. Pure stdlib and deterministic: the plan
is a function of (principal, profile, inbound headers) only, which is what makes the invariant
"no client-asserted identity survives" unit-testable and enforced in the eval gate.
"""

from __future__ import annotations

from collections.abc import Mapping

from hex_service_kit.identity import Principal

from .models import InjectionPlan

PERSONA_HEADER = "x-dev-persona"
IAP_HEADER = "x-goog-iap-jwt-assertion"
#: The same assertion, under a name the serverless frontend does not reserve.
#:
#: ``x-goog-*`` is Google's namespace, and the frontend REMOVES those headers from a request on
#: its way into a service so that only the platform can set them. That protection is right, and
#: it also means the portal cannot hand the assertion to an embedded app under the standard name:
#: the portal injected it, the frontend dropped it, and the app answered "missing IAP assertion
#: header; request did not pass through IAP" -- an accurate message about a request that HAD
#: passed through IAP one hop earlier. Nothing about the trust model changes with the rename: the
#: embedded app still verifies the assertion's signature, issuer and audience against Google's
#: published IAP keys, and it is reachable only by the portal's own service identity, which Cloud
#: Run authenticates independently. The header carries the assertion; it never vouches for it.
PORTAL_IAP_HEADER = "x-portal-iap-assertion"

LOCAL_PROFILE = "local"
SECURE_PROFILES: tuple[str, ...] = ("gcp", "platform")

# Identity headers a browser must never be able to assert to an upstream app. Always stripped
# from the inbound request before the portal injects the identity it verified itself.
CLIENT_SPOOFABLE_IDENTITY: frozenset[str] = frozenset(
    {
        PERSONA_HEADER,
        IAP_HEADER,
        "authorization",
        # IAP's DECODED identity headers. An embedded app must learn who the user is from its
        # own edge, never from something a previous hop copied forward: an app that trusts
        # these from a proxy trusts whatever the proxy was handed.
        "x-goog-authenticated-user-email",
        "x-goog-authenticated-user-id",
        "x-goog-iap-userinfo",
        # The portal-scoped assertion header is injected by the portal and by nothing else. A
        # browser that could set it would be asserting its own identity to the embedded app,
        # which is the exact attack the standard header's reserved namespace prevents.
        PORTAL_IAP_HEADER,
    }
)

# End-to-end / connection-scoped headers a reverse proxy must not forward. ``accept-encoding`` is
# dropped outbound so upstreams reply with identity encoding (the proxy forwards bytes verbatim and
# never has to re-compress), and ``host``/``content-length`` are recomputed by the HTTP client.
_HOP_BY_HOP_REQUEST: frozenset[str] = frozenset(
    {
        "host",
        "content-length",
        # THE hop-scoped credential, and the one easiest to miss because nothing the browser
        # sent contains it. Google's serverless frontend INJECTS `x-serverless-authorization`
        # on the way in, holding a token minted for THIS service. Forwarded verbatim it
        # arrives at the next Cloud Run service, whose frontend prefers it over
        # `authorization` and rejects it, because its audience names the previous hop. The
        # symptom is a 401 "the access token could not be verified" from a callee whose IAM
        # binding, ingress and the caller's freshly minted token are all correct -- a
        # combination that reads as anything except a header the proxy was never meant to copy.
        "x-serverless-authorization",
        "connection",
        "keep-alive",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "accept-encoding",
    }
)

# On the way back, drop the framing headers the client's HTTP layer owns. The upstream body is
# handed to the browser verbatim, so a stale ``content-length``/``content-encoding`` would
# mis-frame it.
_HOP_BY_HOP_RESPONSE: frozenset[str] = frozenset(
    {
        "content-length",
        "content-encoding",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "trailer",
        "upgrade",
        "x-frame-options",
    }
)


def persona_id(principal: Principal) -> str:
    """The seeded-persona id carried in a local :class:`Principal` (``local-persona:analyst`` ->
    ``analyst``); falls back to the subject if the source is not a persona source."""
    _, _, suffix = principal.source.partition(":")
    return suffix or principal.subject


def build_injection_plan(
    principal: Principal,
    profile: str,
    inbound: Mapping[str, str],
) -> InjectionPlan:
    """Build the header-rewrite plan for a request crossing into an embedded app's backend.

    ``inbound`` keys are lower-cased. In every profile the client-spoofable identity headers are
    stripped; the injected identity depends on the profile (see the module docstring). ``onprem``
    injects nothing (its proxy adapter is a fail-fast placeholder).
    """
    set_headers: dict[str, str] = {}
    if profile == LOCAL_PROFILE:
        set_headers[PERSONA_HEADER] = persona_id(principal)
    elif profile in SECURE_PROFILES:
        assertion = inbound.get(IAP_HEADER, "").strip()
        if assertion:
            # Both names: the standard one for any hop that preserves it (a shared edge, a
            # sidecar, an on-prem ingress), and the portal-scoped one for the serverless hop
            # that does not.
            set_headers[IAP_HEADER] = assertion
            set_headers[PORTAL_IAP_HEADER] = assertion
    return InjectionPlan(
        set_headers=tuple(set_headers.items()),
        strip_headers=CLIENT_SPOOFABLE_IDENTITY,
    )


def sanitize_request_headers(inbound: Mapping[str, str], plan: InjectionPlan) -> dict[str, str]:
    """Apply ``plan`` to inbound headers: drop hop-by-hop + stripped identity, then inject.

    The result is the exact header set the portal forwards to the upstream backend. Injected
    (``set``) headers are applied last so they always win over anything that leaked through.
    """
    out: dict[str, str] = {}
    for key, value in inbound.items():
        lower = key.lower()
        if lower in _HOP_BY_HOP_REQUEST or lower in plan.strip_headers:
            continue
        out[lower] = value
    for key, value in plan.set_headers:
        out[key.lower()] = value
    return out


def sanitize_response_headers(
    upstream: tuple[tuple[str, str], ...],
) -> list[tuple[str, str]]:
    """Strip the response framing headers the client's HTTP layer re-derives from the raw body."""
    return [(k, v) for k, v in upstream if k.lower() not in _HOP_BY_HOP_RESPONSE]
