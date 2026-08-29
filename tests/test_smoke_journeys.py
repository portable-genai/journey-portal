"""Offline unit coverage for the live journey smoke runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture(scope="module")
def smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "smoke_journeys.py"
    spec = importlib.util.spec_from_file_location("smoke_journeys_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_SUBJECTS = {
    "analyst": "demo.analyst@bank.example",
    "approver": "demo.approver@bank.example",
}
_REVIEW_ID = "rev-smoke-0001"
_DECISION_PATH = f"/apps/human-review-console/api/v1/reviews/{_REVIEW_ID}/decision"


class _PortalRecorder:
    """A portal that behaves the way the real one does, so the smoke's proofs mean something.

    ``maker`` is who the console reports raised the item, and defaults to the principal the
    portal verifies for the maker persona: the identity proof passes only because the portal
    injected it over the spoofed header.  ``decision`` and ``state`` are what the console
    returns for the withdrawal, so a test can make the queue cleanup fail without touching
    anything else.
    """

    def __init__(
        self,
        module: ModuleType,
        *,
        maker: str = _SUBJECTS["analyst"],
        profile: str = "local",
        decision: str = "allowed",
        state: str = "rejected",
    ) -> None:
        self._module = module
        self._maker = maker
        self._profile = profile
        self._decision = decision
        self._state = state
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append((method, path, payload, headers))
        if path == "/v1/journeys":
            return self._module.JsonResponse(
                200,
                {
                    "journeys": [
                        {
                            "key": "rm",
                            "apps": [
                                {"id": "cdd-sow-research", "api_base": "/agent/api"},
                                {
                                    "id": "loan-document-intelligence",
                                    "api_base": "/apps/loan-document-intelligence/api",
                                },
                                {"id": "cio-advisory", "api_base": "/apps/cio-advisory/api"},
                            ],
                        },
                        {
                            "key": "ops",
                            "apps": [
                                {
                                    "id": "credit-memo-drafting",
                                    "api_base": "/apps/credit-memo-drafting/api",
                                },
                                {
                                    "id": "trade-finance-checker",
                                    "api_base": "/apps/trade-finance-checker/api",
                                },
                                {
                                    "id": "compliance-advisory",
                                    "api_base": "/apps/compliance-advisory/api",
                                },
                                {
                                    "id": "human-review-console",
                                    "api_base": "/apps/human-review-console/api",
                                },
                            ],
                        },
                    ]
                },
            )
        if path.endswith("/healthz"):
            return self._module.JsonResponse(200, {"status": "ok", "profile": self._profile})
        if path == "/v1/session/persona":
            requested = (payload or {}).get("id")
            if requested not in _SUBJECTS:
                raise AssertionError(f"unexpected persona: {requested!r}")
            return self._module.JsonResponse(
                200, {"persona": requested, "subject": _SUBJECTS[requested]}
            )
        if path == "/apps/human-review-console/api/v1/reviews":
            return self._module.JsonResponse(201, {"review_id": _REVIEW_ID, "maker": self._maker})
        if path == _DECISION_PATH:
            return self._module.JsonResponse(
                200, {"decision": self._decision, "item": {"state": self._state}}
            )
        raise AssertionError(f"unexpected portal request: {method} {path}")


def test_smoke_uses_portal_paths_and_proves_selected_actor(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module)

    smoke_module.run_smoke(portal)

    paths = [path for _, path, _, _ in portal.calls]
    assert paths == [
        "/v1/journeys",
        "/agent/api/healthz",
        "/apps/loan-document-intelligence/api/healthz",
        "/apps/cio-advisory/api/healthz",
        "/apps/credit-memo-drafting/api/healthz",
        "/apps/trade-finance-checker/api/healthz",
        "/apps/compliance-advisory/api/healthz",
        "/apps/human-review-console/api/healthz",
        "/v1/session/persona",
        "/apps/human-review-console/api/v1/reviews",
        "/v1/session/persona",
        _DECISION_PATH,
    ]
    assert all(path.startswith("/") for path in paths)
    spoofed = [headers for _, path, _, headers in portal.calls if path.endswith("/v1/reviews")]
    assert spoofed == [{"X-Dev-Persona": "other-tenant"}]


def test_smoke_raises_and_withdraws_under_two_different_personas(
    smoke_module: ModuleType,
) -> None:
    """The item is raised by one persona and disposed of by another.

    They have to differ: the console refuses a self-approval, so an item raised by the only
    persona holding the approver entitlement could never be withdrawn, and every smoke run
    would leave a row in the queue the demonstration presents.
    """
    portal = _PortalRecorder(smoke_module)

    smoke_module.run_smoke(portal)

    selected = [
        (payload or {}).get("id")
        for _, path, payload, _ in portal.calls
        if path == "/v1/session/persona"
    ]
    assert selected == ["analyst", "approver"]
    assert selected[0] != selected[1]


def test_smoke_fails_when_the_queue_would_keep_the_smoke_item(smoke_module: ModuleType) -> None:
    """A refused withdrawal is a failure, not a shrug: the row would stay in the demo queue."""
    portal = _PortalRecorder(smoke_module, decision="denied", state="pending")

    with pytest.raises(smoke_module.SmokeFailure, match="would stay in the demo queue"):
        smoke_module.run_smoke(portal)


def test_smoke_fails_if_real_app_does_not_use_injected_actor(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module, maker="demo.other-tenant@bank.example")

    with pytest.raises(smoke_module.SmokeFailure, match="injected identity proof failed"):
        smoke_module.run_smoke(portal)


def test_smoke_scoped_to_one_journey_checks_only_that_journey(smoke_module: ModuleType) -> None:
    """The portal serves its whole catalog, so a single-journey launch has to say so.

    Without the filter the smoke walks apps the launcher never started and fails on their
    proxied health checks, which is the canonical run order in the demo inventory.
    """
    portal = _PortalRecorder(smoke_module)

    smoke_module.run_smoke(portal, ("ops",))

    paths = [path for _, path, _, _ in portal.calls]
    assert paths == [
        "/v1/journeys",
        "/apps/credit-memo-drafting/api/healthz",
        "/apps/trade-finance-checker/api/healthz",
        "/apps/compliance-advisory/api/healthz",
        "/apps/human-review-console/api/healthz",
        "/v1/session/persona",
        "/apps/human-review-console/api/v1/reviews",
        "/v1/session/persona",
        _DECISION_PATH,
    ]


def test_smoke_skips_the_identity_proof_where_the_console_is_not_mounted(
    smoke_module: ModuleType,
) -> None:
    """`rm` does not embed human-review-console, so the proof is unavailable rather than failed."""
    portal = _PortalRecorder(smoke_module)

    smoke_module.run_smoke(portal, ("rm",))

    paths = [path for _, path, _, _ in portal.calls]
    assert "/v1/session/persona" not in paths
    assert paths == [
        "/v1/journeys",
        "/agent/api/healthz",
        "/apps/loan-document-intelligence/api/healthz",
        "/apps/cio-advisory/api/healthz",
    ]


def test_smoke_refuses_a_journey_the_catalog_does_not_carry(smoke_module: ModuleType) -> None:
    """Checking nothing must not pass: an unknown key is a failure, not an empty selection."""
    portal = _PortalRecorder(smoke_module)

    with pytest.raises(smoke_module.SmokeFailure, match="no journey named nope"):
        smoke_module.run_smoke(portal, ("nope",))


def test_smoke_accepts_a_live_profile(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module, profile="live")

    smoke_module.run_smoke(portal)

    assert [path for _, path, _, _ in portal.calls][0] == "/v1/journeys"


def test_smoke_rejects_any_other_profile(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module, profile="gcp")

    with pytest.raises(smoke_module.SmokeFailure, match="received 'gcp'"):
        smoke_module.run_smoke(portal)


def test_portal_client_rejects_a_non_origin_base_url(smoke_module: ModuleType) -> None:
    with pytest.raises(ValueError, match="must be an origin"):
        smoke_module.PortalClient("http://127.0.0.1:8110/not-the-origin", timeout=1)
