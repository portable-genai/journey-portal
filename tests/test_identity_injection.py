"""The identity trust boundary: a browser-asserted identity NEVER reaches an embedded app.

This is the portal's load-bearing security test. Every case checks two things at once: the
portal-resolved identity is injected, and no client-spoofable identity header survives the hop.
"""

from __future__ import annotations

from hex_service_kit.identity import Principal

from journey_portal.domain.identity_injection import (
    IAP_HEADER,
    PERSONA_HEADER,
    build_injection_plan,
    persona_id,
    sanitize_request_headers,
    sanitize_response_headers,
)


def _forward(principal: Principal, profile: str, inbound: dict[str, str]) -> dict[str, str]:
    lower = {k.lower(): v for k, v in inbound.items()}
    plan = build_injection_plan(principal, profile, lower)
    return sanitize_request_headers(lower, plan)


def test_persona_id_from_source() -> None:
    assert persona_id(Principal(subject="a@b", source="local-persona:approver")) == "approver"
    assert persona_id(Principal(subject="a@b", source="gcp-iap")) == "a@b"


def test_local_injects_resolved_persona_and_strips_spoof() -> None:
    principal = Principal(subject="demo.analyst@bank.example", source="local-persona:analyst")
    forwarded = _forward(
        principal,
        "local",
        {
            "X-Dev-Persona": "approver",  # a browser trying to escalate
            "Authorization": "Bearer forged",
            "Accept-Encoding": "gzip",
            "X-Real-Header": "keep-me",
        },
    )
    # the injected persona is the RESOLVED one, not the spoofed one
    assert forwarded[PERSONA_HEADER] == "analyst"
    # the spoofed identity headers are gone
    assert "authorization" not in forwarded
    assert "approver" not in forwarded.values()
    # hop-by-hop stripped, ordinary headers preserved
    assert "accept-encoding" not in forwarded
    assert forwarded["x-real-header"] == "keep-me"


def test_local_strips_forged_iap_header() -> None:
    principal = Principal(subject="demo.auditor@bank.example", source="local-persona:auditor")
    forwarded = _forward(principal, "local", {IAP_HEADER: "FORGED"})
    assert forwarded[PERSONA_HEADER] == "auditor"
    assert IAP_HEADER not in forwarded  # a browser cannot smuggle an IAP assertion in


def test_secure_forwards_edge_iap_and_strips_browser_identity() -> None:
    principal = Principal(subject="rm@bank.example", tenant="bank", source="gcp-iap")
    forwarded = _forward(
        principal,
        "gcp",
        {
            IAP_HEADER: "EDGE-ASSERTION",  # injected by IAP at the edge: trustworthy
            "X-Dev-Persona": "approver",  # a browser trying to escalate: must not survive
            "Authorization": "Bearer forged",
        },
    )
    assert forwarded[IAP_HEADER] == "EDGE-ASSERTION"
    assert PERSONA_HEADER not in forwarded
    assert "authorization" not in forwarded
    assert "approver" not in forwarded.values()


def test_no_identity_headers_from_a_bare_request() -> None:
    # An unauthenticated inbound request in secure mode injects nothing (the app will 401 itself).
    forwarded = _forward(Principal(subject="", source="gcp-iap"), "gcp", {"accept": "*/*"})
    assert IAP_HEADER not in forwarded
    assert PERSONA_HEADER not in forwarded


def test_response_headers_drop_framing() -> None:
    sanitized = dict(
        sanitize_response_headers(
            (
                ("content-type", "text/html"),
                ("content-length", "999"),
                ("content-encoding", "gzip"),
                ("x-frame-options", "DENY"),
                ("set-cookie", "a=b"),
            )
        )
    )
    assert sanitized == {"content-type": "text/html", "set-cookie": "a=b"}
