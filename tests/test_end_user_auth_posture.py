"""The exposure guard rides the APP OBJECT, and the HOST HEADER is not a peer address.

Two defects, one fix, and the second is why this file exists separately from the tenant-security
tests.

**One.** The only bound on serving the no-auth `local` posture to the network lived in
`resolve_bind_host(...)`, inside `main()`. The shipped entry point never reaches `main()`: the
Dockerfile CMD is

    exec uvicorn journey_portal.api.app:app --host 0.0.0.0 --port ${PORT:-8110}

so the bound was a property of one entry point rather than of the application.

**Two.** `add_tenant_security` looked like it covered the gap, and does not. It rejects a request
whose HOST HEADER has no reviewed tenant policy. The host header is a value the CLIENT writes. A
peer at 203.0.113.7 sending `Host: 127.0.0.1:8110` was answered 200 on `/v1/whoami`, carrying the
full seeded principal, subject and entitlement principals included. The two controls answer
different questions: the tenant check bounds which tenant a request may be framed as, the guard
bounds WHERE the request came from, and neither substitutes for the other. Both are asserted here,
because a fix that quietly dropped the tenant check would pass a test that only looked at peers.

The guard is registered LAST so it is the OUTERMOST middleware: an off-loopback caller is refused
before the tenant-security middleware runs, so the refusal cannot be steered by the header that
middleware reads. Both directions are asserted, because a guard that refuses everybody is not a
fix: a loopback peer must still be served.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from journey_portal.adapters.gcp.identity import IapIdentityAdapter
from journey_portal.adapters.local.identity import LocalIdentityAdapter
from journey_portal.adapters.onprem.identity import OnPremIdentityAdapter
from journey_portal.adapters.platform.identity import PlatformIdentityAdapter
from journey_portal.api.app import app
from journey_portal.config import PROFILES, end_user_auth_kind, identity_adapter_class
from journey_portal.ports.identity import (
    CLIENT_ASSERTED,
    END_USER_AUTH_ATTR,
    END_USER_AUTH_KINDS,
    UNIMPLEMENTED,
    VERIFIED,
    declared_end_user_auth,
)
from tests.conftest import LOOPBACK_PEER

#: A peer somewhere else on the LAN: exactly the address the leak was executed from.
LAN_PEER = ("203.0.113.7", 51234)

#: The header that made the tenant check pass while the request came from the LAN. Kept as a
#: named constant because it IS the bypass, and a test that stopped sending it would go green
#: for the wrong reason.
SPOOFED_LOOPBACK_HOST = {"Host": "127.0.0.1:8110"}

_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_MODULE = _REPO_ROOT / "src" / "journey_portal" / "api" / "app.py"

#: The guard call whose argument must never be derived from a credential.
_GUARD_CALL = "add_loopback_exposure_guard"

#: Anything naming a SERVICE credential. The guard bounds the whole app, including routes that
#: carry no credential at all, so none of these may appear anywhere in the expression that
#: decides whether it is on, at any depth.
_CREDENTIAL_MARKERS: tuple[str, ...] = ("S2S", "TOKEN", "SECRET", "BEARER")


# --------------------------------------------------------------------------- #
# 1. The bypass itself: the spoofed host no longer buys anything.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/v1/whoami", "/v1/personas", "/v1/journeys", "/healthz"])
def test_a_lan_peer_spoofing_a_loopback_host_is_refused(path: str) -> None:
    """The exact executed bypass, as a test.

    `/healthz` is in the parametrisation on purpose: `tenant_security` exempts it from the tenant
    decision entirely (UNAUTHENTICATED_PATHS), so before the guard it answered ANY peer with the
    profile and region regardless of the host header. The guard bounds the whole app.
    """
    response = TestClient(app, client=LAN_PEER).get(path, headers=SPOOFED_LOOPBACK_HOST)
    assert response.status_code == 503, (
        f"{path} was served to a non-loopback peer that merely claimed to be loopback in its Host "
        "header. The tenant check reads a client-supplied value; the peer address is the control."
    )
    detail = response.json()["detail"]
    assert "203.0.113.7" in detail, "the refusal must name the PEER, not the header"
    assert "PORTAL_ALLOW_INSECURE_DEMO" in detail, "the refusal must name the opt-out"


def test_the_refusal_does_not_leak_the_principal() -> None:
    """`/v1/whoami` returned the full seeded principal to the LAN. It must return none of it."""
    body = TestClient(app, client=LAN_PEER).get("/v1/whoami", headers=SPOOFED_LOOPBACK_HOST).text
    assert "demo.analyst@bank.example" not in body
    assert "group:analyst" not in body


# --------------------------------------------------------------------------- #
# 2. The other direction: loopback still works, and the tenant check is INTACT.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/v1/whoami", "/v1/personas", "/v1/journeys", "/healthz"])
def test_a_loopback_peer_is_still_served(path: str) -> None:
    response = TestClient(app, client=LOOPBACK_PEER).get(path, headers=SPOOFED_LOOPBACK_HOST)
    assert response.status_code == 200, (
        f"{path} must still answer a loopback peer: the offline demo, both UI shells and this "
        "suite all reach the BFF that way."
    )


def test_the_principal_still_reaches_a_loopback_caller() -> None:
    whoami = TestClient(app, client=LOOPBACK_PEER).get("/v1/whoami", headers=SPOOFED_LOOPBACK_HOST)
    assert whoami.json()["subject"] == "demo.analyst@bank.example"


def test_the_tenant_check_is_still_enforced_for_a_loopback_caller() -> None:
    """The guard did not REPLACE the tenant control, and this fails if somebody drops it.

    A loopback peer clears the exposure guard, so what answers here is the tenant-security
    middleware: a host with no reviewed policy is still refused, on the same request the guard
    just let through.
    """
    response = TestClient(app, client=LOOPBACK_PEER).get(
        "/v1/whoami", headers={"Host": "not-a-reviewed-tenant.example"}
    )
    assert response.status_code != 200, (
        "the host-header tenant policy check is gone. The exposure guard bounds the PEER; the "
        "tenant check bounds which tenant a request may be framed as. Both are needed."
    )


def test_the_insecure_demo_opt_in_lifts_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's explicit, per-request-read consent. The SAME variable the bind guard uses."""
    monkeypatch.setenv("PORTAL_ALLOW_INSECURE_DEMO", "1")
    assert TestClient(app, client=LAN_PEER).get("/healthz").status_code == 200
    monkeypatch.setenv("PORTAL_ALLOW_INSECURE_DEMO", "true")
    assert TestClient(app, client=LAN_PEER).get("/healthz").status_code == 503


def test_a_forwarding_header_is_disqualifying_even_from_loopback() -> None:
    """A proxy has already overwritten the scope peer, so the header's PRESENCE is the signal."""
    response = TestClient(app, client=LOOPBACK_PEER).get(
        "/healthz", headers={"X-Forwarded-For": "127.0.0.1"}
    )
    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# 3. Every shipped adapter declares what it does, explicitly.
# --------------------------------------------------------------------------- #
def test_the_seeded_persona_adapter_declares_client_asserted() -> None:
    """The persona is picked by the caller, and the portal then INJECTS it into every app."""
    assert declared_end_user_auth(LocalIdentityAdapter) == CLIENT_ASSERTED


def test_the_iap_adapter_declares_that_it_verifies() -> None:
    assert declared_end_user_auth(IapIdentityAdapter) == VERIFIED


def test_the_platform_adapter_inherits_the_verification_and_the_declaration() -> None:
    """The platform adapter subclasses the IAP one, so the claim and the code stay together."""
    assert declared_end_user_auth(PlatformIdentityAdapter) == VERIFIED


def test_the_onprem_placeholder_declares_that_it_verifies_nothing() -> None:
    assert declared_end_user_auth(OnPremIdentityAdapter) == UNIMPLEMENTED


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_every_bound_adapter_declares_explicitly(profile: str) -> None:
    """A new adapter must SAY what it does; inheriting the safe default silently is not enough."""
    adapter = identity_adapter_class(profile)
    declared = [klass for klass in adapter.__mro__ if END_USER_AUTH_ATTR in vars(klass)]
    assert declared, (
        f"{adapter.__name__} (the {profile} identity binding) sets no {END_USER_AUTH_ATTR}. "
        f"Declare one of {sorted(END_USER_AUTH_KINDS)} on the class: the exposure guard reads "
        "it, and silence is read as client-asserted."
    )
    assert declared_end_user_auth(adapter) in END_USER_AUTH_KINDS


class _UndeclaredAdapter:
    """An adapter that says nothing at all."""


class _MisdeclaredAdapter:
    """An adapter whose declaration is a typo, which must not read as a verification claim."""

    end_user_auth = "Verified"


@pytest.mark.parametrize("adapter", [_UndeclaredAdapter, _MisdeclaredAdapter, object()])
def test_silence_and_typos_are_read_as_client_asserted(adapter: object) -> None:
    """The fail-closed default, in the only direction that matters: never VERIFIED."""
    assert declared_end_user_auth(adapter) == CLIENT_ASSERTED


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("local", CLIENT_ASSERTED),
        ("gcp", VERIFIED),
        ("platform", VERIFIED),
        ("onprem", UNIMPLEMENTED),
    ],
)
def test_the_posture_follows_the_profile_binding(profile: str, expected: str) -> None:
    assert end_user_auth_kind(profile) == expected


def test_an_unresolvable_binding_fails_CLOSED_rather_than_raising_past_the_guard() -> None:
    """A guard that switches off because a lookup raised is a guard that fails open."""
    assert end_user_auth_kind("no-such-profile") == CLIENT_ASSERTED


def test_the_posture_follows_a_REBOUND_adapter_not_the_profile_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The on-premises migration path: bind a real verifier and the posture changes with it.

    This is why the guard reads the BINDING rather than the profile string. An adopter who wires
    their own verifying adapter under `onprem` has an authenticated portal, and a guard keyed off
    the word "onprem" would confine it to loopback forever.
    """
    from journey_portal import config as config_module

    monkeypatch.setitem(
        config_module._BINDINGS["identity"],
        "onprem",
        "journey_portal.adapters.gcp.identity:IapIdentityAdapter",
    )
    assert end_user_auth_kind("onprem") == VERIFIED


# --------------------------------------------------------------------------- #
# 4. The guard's argument names no credential, at any depth.
# --------------------------------------------------------------------------- #
class _StripDocstrings(ast.NodeTransformer):
    """Drop every docstring from a subtree before it is scanned.

    The scan looks for the NAME of a credential in what the guard's posture reaches, and a
    docstring is prose, not a read.
    """

    def _strip(self, node: ast.AST) -> ast.AST:
        body = getattr(node, "body", None)
        first = body[0] if isinstance(body, list) and body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]  # type: ignore[attr-defined,index]
        return self.generic_visit(node)

    visit_FunctionDef = _strip
    visit_AsyncFunctionDef = _strip
    visit_ClassDef = _strip
    visit_Module = _strip


def _module_definitions(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = <expr>`` assignments AND function bodies, as source text."""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                found[target.id] = ast.unparse(node.value)
        elif isinstance(node, ast.FunctionDef):
            stripped = _StripDocstrings().visit(ast.parse(ast.unparse(node)))
            found[node.name] = ast.unparse(stripped)
    return found


def guard_posture_source(source: str) -> str:
    """Everything the exposure guard's ``unauthenticated`` argument reaches, as one blob."""
    tree = ast.parse(source)
    definitions = _module_definitions(tree)
    expressions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(_GUARD_CALL):
            expressions += [
                ast.unparse(kw.value) for kw in node.keywords if kw.arg == "unauthenticated"
            ]
    assert expressions, f"no {_GUARD_CALL}(unauthenticated=...) call found"
    seen: set[str] = set()
    reached = list(expressions)
    pending = list(expressions)
    while pending:
        for name_node in ast.walk(ast.parse(pending.pop())):
            if isinstance(name_node, ast.Name) and name_node.id not in seen:
                seen.add(name_node.id)
                if name_node.id in definitions:
                    reached.append(definitions[name_node.id])
                    pending.append(definitions[name_node.id])
    return "\n".join(reached + sorted(seen))


def test_the_exposure_guard_reads_no_service_credential() -> None:
    """A credential may not decide whether the guard is on."""
    reached = guard_posture_source(_APP_MODULE.read_text(encoding="utf-8")).upper()
    offenders = [marker for marker in _CREDENTIAL_MARKERS if marker in reached]
    assert offenders == [], (
        f"the exposure guard's posture reaches {offenders}. A service credential authenticates a "
        "calling SERVICE and no end user. Derive the posture from the identity binding "
        "(config.end_user_auth_kind) instead."
    )


def test_the_exposure_guard_reads_no_host_header() -> None:
    """The other half of the defect: a client-supplied host is not a peer address.

    The bypass was executed by writing `Host: 127.0.0.1:8110`. If the guard's posture ever came
    to read a host or an origin, the same request would defeat it again.
    """
    reached = guard_posture_source(_APP_MODULE.read_text(encoding="utf-8")).upper()
    offenders = [marker for marker in ("HOST", "ORIGIN", "HEADER") if marker in reached]
    assert offenders == [], (
        f"the exposure guard's posture reaches {offenders}, which the CLIENT controls. The guard "
        "judges the ASGI scope's peer address and nothing else."
    )


def test_the_exposure_guard_is_derived_from_the_identity_binding() -> None:
    """Not merely "no credential": the posture must come from the thing that actually knows."""
    reached = guard_posture_source(_APP_MODULE.read_text(encoding="utf-8"))
    assert "end_user_auth_kind" in reached, (
        "the guard no longer reads the identity binding, so nothing checks whether this "
        "deployment can authenticate anybody at all"
    )


#: A posture derived from the host header: the defect's second half, written out. A scanner
#: nobody proved can find anything is a green tick over an empty set.
_MUTANT = (
    "_END_USER_AUTHENTICATED = request_host_is_reviewed(HOST_HEADER)\n"
    "add_loopback_exposure_guard(\n"
    "    app,\n"
    "    unauthenticated=not _END_USER_AUTHENTICATED,\n"
    "    insecure_demo_env='PORTAL_ALLOW_INSECURE_DEMO',\n"
    ")\n"
)


def test_the_scan_finds_the_defect_it_was_written_for() -> None:
    reached = guard_posture_source(_MUTANT).upper()
    caught = {marker for marker in ("HOST", "ORIGIN", "HEADER") if marker in reached}
    assert caught == {"HOST", "HEADER"}, (
        "the scan no longer finds the client-supplied host in the expression the bypass was "
        "written as, so a green result from it means nothing"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
