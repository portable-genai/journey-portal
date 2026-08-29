"""Prove the smoke evaluator cannot certify a metric that measured nothing.

``test_not_falsely_green.py`` proves each scorer can go RED on a degraded case. That is a
different question from this one: a scorer that is never handed a case cannot go red at all.
``smoke()`` selects a metric's cases by exact ``kind`` string, so a renamed or typo'd kind, or a
filtered dataset, removes every case a metric scores while the surviving rows keep ``n_examples``
above the kit's non-empty guard. The old ``_fraction`` returned ``1.0`` for that empty selection,
which is common-base-practices E4 applied per metric: an evaluation that measured nothing
certifying a pass, here for the two security metrics (``identity_isolation``,
``observability_audit_isolation``).

Every assertion below was run against the pre-fix shape first and failed there; see the test
docstrings for what the vacuous form returned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eval.run_eval import DEFAULT_DATASET, METRIC_KINDS, _fraction, _load, smoke


def _dataset_without(kind: str, tmp_path: Path) -> Path:
    """The real golden set with every row of one kind removed: the metric selects nothing."""
    rows = [
        line
        for line in DEFAULT_DATASET.read_text().splitlines()
        if line.strip() and json.loads(line)["kind"] != kind
    ]
    reduced = tmp_path / "reduced.jsonl"
    reduced.write_text("\n".join(rows) + "\n")
    return reduced


def test_empty_selection_scores_zero_not_one() -> None:
    """The vacuous form returned 1.0 here, so this line alone was red before the fix."""
    assert _fraction([]) == 0.0


def test_fraction_still_scores_a_real_selection() -> None:
    assert _fraction([True, True]) == 1.0
    assert _fraction([True, False]) == 0.5


@pytest.mark.parametrize(("metric", "kind"), sorted(METRIC_KINDS.items()))
def test_metric_fails_when_its_kind_has_no_cases(metric: str, kind: str, tmp_path: Path) -> None:
    """Drop one kind: that metric must report FAIL, and the gate must not pass.

    Before the fix every one of these reported ``passed=True`` at a fabricated score of 1.0,
    with the report still showing the surviving rows as ``n_examples``.
    """
    report = smoke(_dataset_without(kind, tmp_path))
    scored = {result.metric: result for result in report.results}
    assert scored[metric].score == 0.0
    assert not scored[metric].passed
    assert not report.passed
    # The rows of the other kinds are still counted, which is exactly why n_examples cannot
    # be the thing that catches this.
    assert report.n_examples > 0


def test_the_real_golden_set_covers_every_metric() -> None:
    """No metric is riding on an empty selection today."""
    kinds = {case["kind"] for case in _load(DEFAULT_DATASET)}
    assert kinds == set(METRIC_KINDS.values())
    report = smoke(DEFAULT_DATASET)
    assert report.passed


def test_load_refuses_a_case_kind_no_metric_scores(tmp_path: Path) -> None:
    """A renamed kind must be refused, not counted toward n_examples and scored by nothing."""
    rows = DEFAULT_DATASET.read_text().splitlines()
    renamed = [
        json.dumps({**json.loads(line), "kind": "observability-audit-v2"})
        if json.loads(line)["kind"] == "observability-audit"
        else line
        for line in rows
        if line.strip()
    ]
    dataset = tmp_path / "renamed.jsonl"
    dataset.write_text("\n".join(renamed) + "\n")
    with pytest.raises(ValueError, match="observability-audit-v2"):
        _load(dataset)
