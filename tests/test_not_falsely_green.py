"""Prove each eval metric can go RED: a degraded input must score below threshold.

Guards against a falsely-green metric that cannot detect the defect it exists to catch (the
identity-isolation metric especially: a portal that leaks a spoofed identity must fail).
"""

from __future__ import annotations

from agent_eval_kit import assert_can_go_red
from eval.run_eval import (
    THRESHOLDS,
    config_case_ok,
    identity_case_ok,
    observability_case_ok,
    policy_case_ok,
    routing_case_ok,
)


def test_journey_integrity_can_go_red() -> None:
    good = {
        "valid": True,
        "config": {
            "apps": {
                "a": {"label": "A", "ui_upstream": "http://x:1", "api_upstream": "http://x:2"}
            },
            "journeys": {"j": {"label": "J", "blurb": "b", "apps": ["a"]}},
        },
    }
    # a config that references an unknown app but is LABELLED valid: the metric must catch the lie
    bad = {
        "valid": True,
        "config": {
            "apps": {
                "a": {"label": "A", "ui_upstream": "http://x:1", "api_upstream": "http://x:2"}
            },
            "journeys": {"j": {"label": "J", "blurb": "b", "apps": ["missing"]}},
        },
    }
    assert_can_go_red(
        lambda c: 1.0 if config_case_ok(c) else 0.0,
        green=good,
        red=bad,
        threshold=THRESHOLDS["journey_integrity"],
        metric="journey_integrity",
    )


def test_identity_isolation_can_go_red() -> None:
    case = {"forbid_values": ["approver"], "expect_set": {"x-dev-persona": "analyst"}}
    clean = {"x-dev-persona": "analyst"}  # only the resolved identity present
    leaked = {"x-dev-persona": "approver"}  # the spoofed persona survived: MUST fail
    assert_can_go_red(
        lambda fwd: 1.0 if identity_case_ok(fwd, case) else 0.0,
        green=clean,
        red=leaked,
        threshold=THRESHOLDS["identity_isolation"],
        metric="identity_isolation",
    )


def test_routing_correctness_can_go_red() -> None:
    good = {
        "fn": "api",
        "ui_upstream": "http://u:1",
        "api_upstream": "http://b:2",
        "arg": "v1/cdd",
        "expect": "http://b:2/v1/cdd",
    }
    bad = {**good, "expect": "http://b:2/WRONG"}
    assert_can_go_red(
        lambda c: 1.0 if routing_case_ok(c) else 0.0,
        green=good,
        red=bad,
        threshold=THRESHOLDS["routing_correctness"],
        metric="routing_correctness",
    )


def test_tenant_policy_isolation_can_go_red() -> None:
    good = {
        "host": "journey.fictional-bank.test",
        "tenant": "fictional-bank",
        "origin": "",
        "expect_decision": "allowed",
        "expect_cors": False,
        "expect_frame_ancestors": ["'self'", "https://host.fictional-bank.test"],
    }
    bad = {**good, "expect_decision": "denied"}
    assert_can_go_red(
        lambda case: 1.0 if policy_case_ok(case) else 0.0,
        green=good,
        red=bad,
        threshold=THRESHOLDS["tenant_policy_isolation"],
        metric="tenant_policy_isolation",
    )


def test_observability_audit_isolation_can_go_red() -> None:
    good = {
        "action": "forward:api",
        "expect_decision": "allowed",
    }
    bad = {**good, "expect_decision": "blocked"}
    assert_can_go_red(
        lambda case: 1.0 if observability_case_ok(case) else 0.0,
        green=good,
        red=bad,
        threshold=THRESHOLDS["observability_audit_isolation"],
        metric="observability_audit_isolation",
    )
