"""Every metric in this gate is shown failing the defect it exists to catch.

This repository is not agentic: every metric is a deterministic security or composition
invariant, which is the right reading of an eval for a trust boundary. What a portal owes is not
"a good answer" but "the same refusal, every time", and that makes falsification simple and makes
skipping it inexcusable.

Each predicate is IMPORTED from ``eval.run_eval`` rather than re-implemented, so a predicate that
silently became a constant breaks this build. A proof written against a local copy shows the copy
works while the gate stays blind.
"""

from __future__ import annotations

import pytest
from agent_eval_kit import assert_can_go_red
from agent_eval_kit.rubrics import RubricError
from eval.run_eval import (
    DEFAULT_DATASET,
    METRIC_KINDS,
    RUBRICS,
    THRESHOLDS,
    _fraction,
    _load,
    config_case_ok,
    csrf_case_ok,
    host_proof_case_ok,
    identity_case_ok,
    identity_forwarded,
    observability_case_ok,
    policy_case_ok,
    routing_case_ok,
)

_CASES = _load(DEFAULT_DATASET)


def _of_kind(kind: str) -> list[dict]:
    return [case for case in _CASES if case["kind"] == kind]


def _first(kind: str, *, accept: bool) -> dict:
    for case in _of_kind(kind):
        if bool(case.get("accept", case.get("valid"))) is accept:
            return case
    raise AssertionError(f"no {kind!r} case with accept={accept}; the corpus is one-sided")


# --------------------------------------------------------------------------- #
# The two new kinds: the CSRF token and the browser-provenance proof
# --------------------------------------------------------------------------- #
def test_a_csrf_verifier_that_accepts_anything_is_caught() -> None:
    """The corpus carries the refusals, so a verifier that never refuses scores red.

    This is the direction that matters: a CSRF check which accepts everything is indistinguishable
    from no CSRF check, and it is what a token layer degrades into when a binding stops being
    compared.
    """
    cases = _of_kind("csrf")
    assert_can_go_red(
        lambda predicate: _fraction([predicate(case) for case in cases]),
        green=csrf_case_ok,
        red=lambda case: bool(case["accept"]),  # a verifier that accepts every token
        threshold=THRESHOLDS["csrf_token_integrity"],
        metric="csrf_token_integrity",
    )


def test_a_provenance_check_that_accepts_anything_is_caught() -> None:
    cases = _of_kind("host-proof")
    assert_can_go_red(
        lambda predicate: _fraction([predicate(case) for case in cases]),
        green=host_proof_case_ok,
        red=lambda case: bool(case["accept"]),  # a check that accepts every origin
        threshold=THRESHOLDS["host_proof_integrity"],
        metric="host_proof_integrity",
    )


def test_both_new_corpora_carry_refusals_as_well_as_acceptances() -> None:
    """A one-directional corpus certifies a predicate that always answers one way.

    The refusals are the interesting half in both families: a look-alike origin, an unlabelled
    fetch site, a token from another session, an expired one. A corpus of acceptances alone would
    score a predicate that accepts everything at a perfect 1.000.
    """
    for kind in ("csrf", "host-proof"):
        accepted = [case for case in _of_kind(kind) if case["accept"]]
        refused = [case for case in _of_kind(kind) if not case["accept"]]
        assert accepted, f"{kind}: no accepted case, so nothing proves the check can say yes"
        assert len(refused) >= 3, f"{kind}: too few refusals to describe how it says no"


# --------------------------------------------------------------------------- #
# The five original kinds
# --------------------------------------------------------------------------- #
def test_journey_integrity_can_go_red() -> None:
    """The red case is a scorer that reports every configuration as matching its label.

    `config_case_ok` compares the builder's verdict with the reviewer's `valid` flag, so a
    "builder that accepts everything" is the wrong mutant: it agrees with every case the reviewer
    also marked valid, and the metric would still score above zero rather than below the bar. The
    mutant that matters is the comparison itself collapsing into a constant, which is exactly what
    a refactor produces.
    """
    assert_can_go_red(
        lambda predicate: _fraction([predicate(case) for case in _of_kind("config")]),
        green=config_case_ok,
        red=lambda _case: False,  # a comparison that never agrees with the reviewer
        threshold=THRESHOLDS["journey_integrity"],
        metric="journey_integrity",
    )


def test_identity_isolation_can_go_red() -> None:
    """The red case forwards the inbound headers unchanged, spoofed values included."""
    cases = _of_kind("identity")
    assert_can_go_red(
        lambda forward: _fraction([identity_case_ok(forward(case), case) for case in cases]),
        green=identity_forwarded,
        red=lambda case: {k.lower(): v for k, v in case["inbound"].items()},
        threshold=THRESHOLDS["identity_isolation"],
        metric="identity_isolation",
    )


def test_routing_correctness_can_go_red() -> None:
    assert_can_go_red(
        lambda predicate: _fraction([predicate(case) for case in _of_kind("routing")]),
        green=routing_case_ok,
        red=lambda _case: False,  # a router that never reaches the expected target
        threshold=THRESHOLDS["routing_correctness"],
        metric="routing_correctness",
    )


def test_tenant_policy_isolation_can_go_red() -> None:
    assert_can_go_red(
        lambda predicate: _fraction([predicate(case) for case in _of_kind("tenant-policy")]),
        green=policy_case_ok,
        red=lambda _case: False,
        threshold=THRESHOLDS["tenant_policy_isolation"],
        metric="tenant_policy_isolation",
    )


def test_observability_audit_isolation_can_go_red() -> None:
    assert_can_go_red(
        lambda predicate: _fraction([predicate(case) for case in _of_kind("observability-audit")]),
        green=observability_case_ok,
        red=lambda _case: False,
        threshold=THRESHOLDS["observability_audit_isolation"],
        metric="observability_audit_isolation",
    )


# --------------------------------------------------------------------------- #
# The structural refusals
# --------------------------------------------------------------------------- #
def test_an_empty_selection_scores_zero_and_never_one() -> None:
    """A metric whose kind selected no row measured nothing, and nothing is not evidence."""
    assert _fraction([]) == 0.0


def test_every_scored_metric_has_a_reviewed_bar_and_every_bar_is_scored() -> None:
    """Both directions. This repository had no rubric directory at all until now."""
    from agent_eval_kit import load_rubrics

    load_rubrics(RUBRICS).assert_covers(METRIC_KINDS)
    with pytest.raises(RubricError, match="reads as governance"):
        load_rubrics(RUBRICS).assert_covers(list(METRIC_KINDS)[:-1])


def test_a_case_kind_no_metric_scores_is_refused(tmp_path) -> None:
    """The quiet half of the E4 failure: the row still counts toward n_examples.

    A renamed or mistyped `kind` leaves the row in the file, so the report looks evidenced, while
    the metric that should have selected it selects nothing and scores a vacuous number. `_load`
    refuses the dataset rather than scoring a lie.
    """
    dataset = tmp_path / "golden.jsonl"
    dataset.write_text('{"kind": "rooting", "id": "typo"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="scored by no metric"):
        _load(dataset)
