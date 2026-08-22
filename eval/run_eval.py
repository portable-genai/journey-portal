#!/usr/bin/env python3
"""Evaluation gate for the Hrz9 Journey Portal Shell.

Two named layers via ``--mode`` (the scaffold is ``agent_eval_kit.eval_main``):

* **smoke** (default) - the offline pre-merge check CI runs on every change. It scores the pure,
  deterministic portal core against one golden set with SDK-free code, in four metrics:
    - ``journey_integrity``: the catalog builder ACCEPTS every well-formed config and REJECTS
      every malformed one (unknown app ref, non-http upstream, bad id, duplicate). Safety-ish
      config correctness; threshold 0.99.
    - ``identity_isolation``: for every (principal, profile, inbound) case the forwarded headers
      carry the portal-resolved identity and NEVER a browser-asserted one. This is THE security
      invariant of a portal that fronts many apps, so its threshold is 0.99.
    - ``routing_correctness``: the reverse-proxy target URLs (api prefix stripped, ui full path)
      match the hand-computed golden; threshold 0.99.
    - ``tenant_policy_isolation``: exact host, verified tenant and Origin inputs match the
      independently declared allow/deny and framing outcomes; threshold 0.99.
    - ``hrz5_audit_isolation``: central audit mapping preserves keyed references and bounded
      metadata while emitting no prompt, response or citation content; threshold 1.00.
* **gate** - the promotion verdict from the shared Hrz4 authority (requires the platform / gcp
  profile), via ``agent_eval_kit.PromotionGateClient``.

Exit is ``0`` iff every metric meets its threshold (and, in gate mode, the authority agrees).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_eval_kit import EvalMetricResult, EvalReport, PromotionGateClient, eval_main
from hex_service_kit.identity import Principal
from hex_service_kit.netdefaults import read_env_setting

from journey_portal.domain.catalog import JourneyCatalog, api_target, ui_target
from journey_portal.domain.embed_policy import TenantEmbedPolicyService
from journey_portal.domain.errors import JourneyConfigError
from journey_portal.domain.hrz5_audit import to_hrz5_audit_event
from journey_portal.domain.identity_injection import (
    build_injection_plan,
    sanitize_request_headers,
)
from journey_portal.domain.models import AppMount, PortalAccessEvent, TenantEmbedPolicy

THRESHOLDS: dict[str, float] = {
    "journey_integrity": 0.99,
    "identity_isolation": 0.99,
    "routing_correctness": 0.99,
    "tenant_policy_isolation": 0.99,
    "hrz5_audit_isolation": 1.0,
}

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = _REPO_ROOT / "eval" / "datasets" / "golden_journeys.jsonl"


def _load(dataset: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# Per-case predicates (reused by the not-falsely-green test)
# --------------------------------------------------------------------------- #
def config_case_ok(case: dict[str, Any]) -> bool:
    """The builder's accept/reject verdict matches the golden ``valid`` flag."""
    try:
        JourneyCatalog.from_mapping(case["config"])
        accepted = True
    except (JourneyConfigError, TypeError, KeyError):
        accepted = False
    return accepted is bool(case["valid"])


def identity_forwarded(case: dict[str, Any]) -> dict[str, str]:
    """The exact headers the portal would forward upstream for this case."""
    principal = Principal(subject="user@bank.example", source=case["source"])
    inbound = {k.lower(): v for k, v in case["inbound"].items()}
    plan = build_injection_plan(principal, case["profile"], inbound)
    return sanitize_request_headers(inbound, plan)


def identity_case_ok(forwarded: dict[str, str], case: dict[str, Any]) -> bool:
    """Injected identity present with the exact value; no forbidden (spoofed) value survives."""
    for key, value in case.get("expect_set", {}).items():
        if forwarded.get(key.lower()) != value:
            return False
    forbidden = set(case.get("forbid_values", []))
    return not (forbidden & set(forwarded.values()))


def routing_case_ok(case: dict[str, Any]) -> bool:
    mount = AppMount(
        app_id="doc1",
        label="x",
        ui_upstream=case["ui_upstream"],
        api_upstream=case["api_upstream"],
    )
    got = api_target(mount, case["arg"]) if case["fn"] == "api" else ui_target(mount, case["arg"])
    return got == case["expect"]


def policy_case_ok(case: dict[str, Any]) -> bool:
    service = TenantEmbedPolicyService(
        (
            TenantEmbedPolicy(
                policy_id="fictional-bank-policy-v1",
                tenant="fictional-bank",
                hosts=("journey.fictional-bank.test",),
                frame_ancestors=("'self'", "https://host.fictional-bank.test"),
                cors_origins=("https://host.fictional-bank.test",),
            ),
        )
    )
    assessment = service.assess(
        request_host=case["host"],
        tenant=case["tenant"],
        request_origin=case.get("origin", ""),
    )
    return (
        assessment.decision == case["expect_decision"]
        and assessment.cors_allowed is case["expect_cors"]
        and list(assessment.frame_ancestors) == case["expect_frame_ancestors"]
    )


def hrz5_case_ok(case: dict[str, Any]) -> bool:
    event = PortalAccessEvent(
        event_id="fictional-eval-event",
        occurred_at="2026-07-29T12:00:00+00:00",
        actor_ref="actor:v1:fictional-pseudonym",
        tenant_ref="tenant:v1:fictional-pseudonym",
        pseudonym_key_id="fictional-key-v1",
        method="POST",
        action=case["action"],
        app_id=case.get("app_id", "doc1"),
    )
    payload = to_hrz5_audit_event(event)
    return (
        payload["decision"] == case["expect_decision"]
        and payload["actor"] == event.actor_ref
        and payload["redacted_prompt"] == ""
        and payload["redacted_response"] == ""
        and payload["citations"] == []
        and payload["metadata"]
        == {
            "event_id": event.event_id,
            "method": event.method,
            "pseudonym_key_id": event.pseudonym_key_id,
            "source": "hrz9-journey-portal",
            "tenant_ref": event.tenant_ref,
        }
        and (
            not case.get("forbidden")
            or str(case["forbidden"]) not in json.dumps(payload, sort_keys=True)
        )
    )


# --------------------------------------------------------------------------- #
# Smoke evaluator + gate runner
# --------------------------------------------------------------------------- #
def _fraction(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 1.0


def smoke(dataset: Path) -> EvalReport:
    cases = _load(dataset)
    config = [config_case_ok(c) for c in cases if c["kind"] == "config"]
    identity = [
        identity_case_ok(identity_forwarded(c), c) for c in cases if c["kind"] == "identity"
    ]
    routing = [routing_case_ok(c) for c in cases if c["kind"] == "routing"]
    tenant_policy = [policy_case_ok(c) for c in cases if c["kind"] == "tenant-policy"]
    hrz5_audit = [hrz5_case_ok(c) for c in cases if c["kind"] == "hrz5-audit"]
    results = (
        EvalMetricResult.scored(
            "journey_integrity", _fraction(config), THRESHOLDS["journey_integrity"]
        ),
        EvalMetricResult.scored(
            "identity_isolation", _fraction(identity), THRESHOLDS["identity_isolation"]
        ),
        EvalMetricResult.scored(
            "routing_correctness", _fraction(routing), THRESHOLDS["routing_correctness"]
        ),
        EvalMetricResult.scored(
            "tenant_policy_isolation",
            _fraction(tenant_policy),
            THRESHOLDS["tenant_policy_isolation"],
        ),
        EvalMetricResult.scored(
            "hrz5_audit_isolation",
            _fraction(hrz5_audit),
            THRESHOLDS["hrz5_audit_isolation"],
        ),
    )
    return EvalReport(dataset=str(dataset), results=results, n_examples=len(cases))


def gate(dataset: Path) -> tuple[EvalReport, bool]:
    gate_url = read_env_setting("PORTAL_GATE_URL")
    bundle = read_env_setting("PORTAL_BUNDLE_ID")
    model = read_env_setting("PORTAL_GATE_MODEL")
    if not gate_url.has_value:
        raise ValueError("PORTAL_GATE_URL must name the promotion authority in gate mode")
    if bundle.is_configured_empty or model.is_configured_empty:
        name = bundle.name if bundle.is_configured_empty else model.name
        raise ValueError(
            f"{name} is set but empty; unset it to use the documented gate default, "
            "or provide a non-empty value"
        )
    client = PromotionGateClient(
        base_url=gate_url.value,
        bundle=bundle.value or "local",
        model=model.value or "deterministic-portal-core",
    )
    report = client.evaluate(str(dataset))
    passed = client.gate(str(dataset))
    return report, passed


if __name__ == "__main__":
    raise SystemExit(
        eval_main(
            smoke=smoke,
            gate=gate,
            default_dataset=DEFAULT_DATASET,
            description="Journey Portal offline / remote evaluation gate.",
        )
    )
