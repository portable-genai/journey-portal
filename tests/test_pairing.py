"""The paired comparison must be able to FAIL, and must fail for the right reasons.

A comparison observed only green is indistinguishable from one that asserts nothing. These tests
are the offline half of check F4: they run with no browser, no deployment and no credential, and
they prove that each class of divergence the invariant cares about is actually caught, and that
each declared reduction is actually tolerated.

The live half is ``make e2e-local && make e2e-gcp && make e2e-pair``.
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from pairing import EXEMPT, PairingError, comparable, compare  # noqa: E402


def _dossier() -> dict[str, Any]:
    """A dossier shaped like the wire response of ``POST /v1/cdd``."""
    return {
        "id": "case-local-001",
        "subject": {"id": "subj-1", "name": "Meridian Harbour Holdings Pte Ltd"},
        "generated_at": "2026-08-25T12:08:00Z",
        "requires_human_review": True,
        "rating": {
            "band": "HIGH",
            "score": 0.72,
            "rationale": "A short local narration.",
            "requires_human_review": True,
            "factors": [
                {"id": "jurisdiction", "weight": 0.3, "value": 0.9, "band": "HIGH"},
                {"id": "ownership_opacity", "weight": 0.2, "value": 0.4, "band": "MEDIUM"},
            ],
            "citations": [
                {
                    "source_id": "doc-1",
                    "source_type": "document",
                    "title": "Source of wealth statement",
                    "page": 1,
                    "snippet": "locally extracted span",
                    "score": 0.41,
                }
            ],
        },
        "sow": {
            "subject_id": "subj-1",
            "narrative": "Short.",
            "confidence": 0.8,
            "requires_human_review": False,
            "sources": [{"kind": "business_sale", "amount": 12400000, "currency": "SGD"}],
            "citations": [
                {"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 2}
            ],
        },
        "screening": {
            "query_name": "Meridian Harbour Holdings Pte Ltd",
            "lists_version": "2026-08-01",
            "sources": ["ofac", "un"],
            "alerts": [],
            "screened_at": "2026-08-25T12:07:00Z",
        },
        "adverse_media": None,
    }


def _pair(mutate) -> Any:
    left = _dossier()
    right = deepcopy(left)
    mutate(right)
    return compare(left, right, "local", "gcp")


def test_identical_dossiers_agree() -> None:
    report = _pair(lambda d: None)
    assert report.agreed
    assert report.compared, "an agreement over zero fields is the vacuous pass, not a pass"


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("the risk band", lambda d: d["rating"].__setitem__("band", "LOW")),
        ("the risk score", lambda d: d["rating"].__setitem__("score", 0.31)),
        (
            "the escalation decision",
            lambda d: d.__setitem__("requires_human_review", False),
        ),
        (
            "a scorecard factor's weight",
            lambda d: d["rating"]["factors"][0].__setitem__("weight", 0.9),
        ),
        (
            "a dropped citation",
            lambda d: d["rating"].__setitem__("citations", []),
        ),
        (
            "a claim grounded in a different KIND of source",
            lambda d: d["rating"]["citations"][0].__setitem__("source_type", "web"),
        ),
        (
            "a citation whose title decayed into its own opaque id",
            lambda d: d["rating"]["citations"][0].__setitem__("title", "doc-1"),
        ),
        (
            "a source-of-wealth amount",
            lambda d: d["sow"]["sources"][0].__setitem__("amount", 1),
        ),
        (
            "the screening list version",
            lambda d: d["screening"].__setitem__("lists_version", "2020-01-01"),
        ),
        (
            "an alert that only one profile raised",
            lambda d: d["screening"].__setitem__(
                "alerts", [{"list": "ofac", "matched_name": "A. Tan", "open": True}]
            ),
        ),
        (
            "a screen one profile silently did not run",
            lambda d: d.__setitem__("screening", None),
        ),
    ],
)
def test_policy_divergence_is_caught(name: str, mutate) -> None:
    """Every one of these is 'figures, checks, escalation reasons, citation relationships'."""
    report = _pair(mutate)
    assert not report.agreed, f"{name} diverged and the comparison called it a match"
    assert report.divergences[0].field


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "narration length",
            lambda d: d["rating"].__setitem__(
                "rationale", "A far longer narration from a frontier model. " * 40
            ),
        ),
        (
            "the source-of-wealth narrative",
            lambda d: d["sow"].__setitem__("narrative", "Much longer prose. " * 60),
        ),
        (
            "a citation snippet extracted by a different parser",
            lambda d: d["rating"]["citations"][0].__setitem__("snippet", "a different span"),
        ),
        (
            "retrieval relevance ranking",
            lambda d: d["rating"]["citations"][0].__setitem__("score", 0.99),
        ),
        ("timestamps", lambda d: d.__setitem__("generated_at", "2027-01-01T00:00:00Z")),
        ("per-run identifiers", lambda d: d.__setitem__("id", "case-gcp-777")),
        (
            "the same document minted a different id in the other profile's store",
            lambda d: d["rating"]["citations"][0].__setitem__("source_id", "doc-other-store"),
        ),
        (
            "adverse media, searched on one profile and not the other",
            lambda d: d.__setitem__(
                "adverse_media", {"subject_name": "x", "findings": [], "sources": ["web"]}
            ),
        ),
    ],
)
def test_declared_reductions_are_tolerated(name: str, mutate) -> None:
    """Quality, scale and durability may move. If these went red the gate would be unusable."""
    report = _pair(mutate)
    assert report.agreed, f"{name} is a declared reduction and must not fail the pair"


def test_every_exemption_states_a_reason() -> None:
    """An exemption without a reason is where a real divergence goes to be forgotten."""
    for key, reason in EXEMPT.items():
        assert len(reason) > 20, f"{key} is exempt without a stated reason"


def test_an_empty_dossier_is_a_failure_not_a_match() -> None:
    """``all(())`` is vacuously true. Two empty answers must never pair."""
    with pytest.raises(PairingError):
        compare({}, {}, "local", "gcp")


def test_a_non_object_response_is_refused() -> None:
    with pytest.raises(PairingError):
        comparable(["not", "a", "dossier"])  # type: ignore[arg-type]


def test_present_versus_absent_is_itself_compared() -> None:
    """A profile that omits ownership must not read as agreeing with one that resolved it."""
    left = _dossier()
    right = deepcopy(left)
    right["ownership"] = {"root_entity": "Meridian", "owners": [], "citations": []}
    report = compare(left, right, "local", "gcp")
    assert not report.agreed
    assert any(d.field == "ownership.present" for d in report.divergences)


# --------------------------------------------------------------------------------------- #
# What a citation comparison is actually asserting.
#
# The first live pair reported "count 3 vs 8" and stopped there, which reads as a policy
# divergence and was two different things wearing one number: a store that returned one
# document as eight extractive segments, and eight titles that had each decayed into their
# own document id. Neither is a count of sources. These tests hold the comparison to the
# thing the invariant names -- WHICH documents ground a claim -- so re-chunking cannot make
# the pair red and a lost source name cannot leave it green.
# --------------------------------------------------------------------------------------- #
def _with_citations(dossier: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any]:
    dossier["sow"]["citations"] = citations
    return dossier


def test_the_same_document_split_differently_is_not_a_divergence() -> None:
    """Parser fidelity. One document, three segments on one side and one on the other."""

    left = _with_citations(
        _dossier(),
        [
            {"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 1},
            {"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 2},
            {"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 3},
        ],
    )
    right = _with_citations(
        _dossier(),
        [{"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 9}],
    )

    report = compare(left, right, "local", "gcp")

    assert report.agreed, [d.field for d in report.divergences]


def test_a_claim_grounded_in_a_different_document_is_a_divergence() -> None:
    """The property re-chunking tolerance must not cost: WHICH documents still matters."""

    left = _with_citations(
        _dossier(),
        [{"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 1}],
    )
    right = _with_citations(
        _dossier(),
        [{"source_id": "doc-2", "source_type": "document", "title": "Something else", "page": 1}],
    )

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in [d.field for d in report.divergences]


def test_a_second_distinct_document_is_a_divergence_not_a_chunk() -> None:
    """Grouping by source_id must not let a genuinely extra source hide as a segment."""

    left = _with_citations(
        _dossier(),
        [{"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 1}],
    )
    right = _with_citations(
        _dossier(),
        [
            {"source_id": "doc-1", "source_type": "document", "title": "SoW", "page": 1},
            {"source_id": "doc-2", "source_type": "document", "title": "SoW", "page": 1},
        ],
    )

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in [d.field for d in report.divergences]


def test_a_title_that_decayed_into_its_own_id_is_caught() -> None:
    """The managed defect itself: same document, same count, name lost."""

    left = _with_citations(
        _dossier(),
        [{"source_id": "doc-1", "source_type": "document", "title": "Bank statement"}],
    )
    right = _with_citations(
        _dossier(),
        [{"source_id": "doc-1", "source_type": "document", "title": "doc-1"}],
    )

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    diverged = {d.field for d in report.divergences}
    assert "sow.citations" in diverged


def test_two_distinct_documents_sharing_a_title_are_not_collapsed() -> None:
    """Titles are compared as a multiset. Two bank statements are two sources."""

    left = _with_citations(
        _dossier(),
        [
            {"source_id": "doc-1", "source_type": "document", "title": "bank_statement"},
            {"source_id": "doc-2", "source_type": "document", "title": "bank_statement"},
        ],
    )
    right = _with_citations(
        _dossier(),
        [{"source_id": "doc-1", "source_type": "document", "title": "bank_statement"}],
    )

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in [d.field for d in report.divergences]
