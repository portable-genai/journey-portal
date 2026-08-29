"""The paired comparison must be able to FAIL, and must fail for the right reasons.

A comparison observed only green is indistinguishable from one that asserts nothing. These tests
are the offline half of check F4: they run with no browser, no deployment and no credential, and
they prove that each class of divergence the invariant cares about is actually caught, and that
each declared reduction is actually tolerated.

The live half is ``make e2e-local && make e2e-gcp && make e2e-pair``.

**The fixture is the wire, and that is load-bearing.** Until 2026-08-29 ``_dossier()`` was shaped
like the comparator's imagination rather than like ``POST /v1/cdd``: its sources carried ``amount``
and ``currency``, its factors ``value`` and ``band``, its alerts ``list`` and ``open``. Not one of
those six fields exists on any response. So the comparator read them, this suite fed them, every
"can fail" case here passed, and against real traffic the same comparisons were comparing ``None``
with ``None``. A fixture invented alongside the code it certifies certifies nothing. The fixture
below is the shape of a captured ``e2e/out/<target>/dossier.json``, ``test_the_fixture_is_the_wire``
holds it to the API's own Pydantic models field for field, and
``test_no_compared_field_is_dead_against_the_wire`` fails if the comparator ever again reads a
field the wire does not send.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from pairing import (  # noqa: E402
    CAPTURE_STEP,
    EXEMPT,
    ONE_WAY,
    PairingError,
    _value_band,
    comparable,
    compare,
    load_run,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _citation(source_id: str = "doc-1", title: str = "bank_statement", **overrides: Any) -> Any:
    """One citation, carrying every member ``CitationModel`` publishes and no other."""
    citation = {
        "source_id": source_id,
        "source_type": "document",
        "title": title,
        "url": f"/v1/cases/meridian/documents/{source_id}",
        "page": 1,
        "snippet": "Sale consideration: SGD 12,400,000, received 2019-11-04.",
        "score": 0.5,
        "continuation_id": "",
    }
    citation.update(overrides)
    return citation


#: One watchlist alert, wire-shaped. Both captured artifacts currently carry an empty ``alerts``
#: list, so this is the only place the alert comparison is exercised at all: if it is not the
#: wire's shape here, the comparison is unproved everywhere.
ALERT: dict[str, Any] = {
    "id": "alert-1",
    "status": "PENDING",
    "score": 0.94,
    "matched_name": "A. Tan",
    "features": ["name_exact"],
    "entry": {
        "uid": "ofac-sdn-0001",
        "source": "ofac_sdn",
        "name": "A. Tan",
        "entity_type": "individual",
        "aliases": [],
        "dob": None,
        "countries": ["SG"],
        "programs": ["SDGT"],
    },
}


def _dossier() -> dict[str, Any]:
    """A dossier shaped like the wire response of ``POST /v1/cdd``, field for field.

    The subject is the fictional demo entity the e2e journey itself uses, so nothing real travels
    into a fixture. The values are the laptop profile's: ``adverse_media`` null because the
    offline adapter has no public web to search.
    """
    return {
        "id": "cdd-meridian-harbour-holdings-pte-ltd",
        "subject": {
            "id": "meridian-harbour-holdings-pte-ltd",
            "name": "Meridian Harbour Holdings Pte Ltd",
            "type": "entity",
            "jurisdiction": "SG",
            "dob_or_incorp": None,
        },
        "generated_at": "2026-08-29T12:08:00+00:00",
        "requires_human_review": True,
        "rating": {
            "band": "medium",
            "score": 0.25,
            "rationale": "A short local narration.",
            "requires_human_review": True,
            "factors": [
                {
                    "name": "customer_type",
                    "weight": 0.15,
                    "present": True,
                    "detail": "entity customer.",
                    "citations": [],
                },
                {
                    "name": "adverse_media",
                    "weight": 0.15,
                    "present": False,
                    "detail": "No adverse media found.",
                    "citations": [],
                },
            ],
            "citations": [_citation()],
        },
        "sow": {
            "subject_id": "meridian-harbour-holdings-pte-ltd",
            "narrative": "Short.",
            "confidence": 0.86,
            "requires_human_review": True,
            "sources": [
                {
                    "kind": "business_ownership",
                    "description": "Majority shareholding in a logistics business.",
                    "est_value_band": "USD 1m-5m",
                    "citations": [_citation()],
                },
                {
                    "kind": "asset_sale",
                    "description": "One-off gain from a residential property sale.",
                    "est_value_band": "USD 100k-1m",
                    "citations": [_citation()],
                },
            ],
            "citations": [_citation()],
        },
        "screening": {
            "query_name": "Meridian Harbour Holdings Pte Ltd",
            "lists_version": "2026-03-01-fixture-v1",
            "sources": ["ofac_sdn", "un"],
            "alerts": [],
            "screened_at": "2026-08-29T12:07:00+00:00",
        },
        "ownership": {
            "root_entity": "Meridian Harbour Holdings Pte Ltd",
            "owners": [
                {
                    "name": "Beneficial owner of Meridian Harbour Holdings (FICTIONAL)",
                    "pct": 75.0,
                    "country": "SG",
                    "is_pep": False,
                    "citations": [_citation(source_id="doc-registry", title="registry")],
                }
            ],
            "tree": None,
            "citations": [_citation(source_id="doc-registry", title="registry")],
        },
        "adverse_media": None,
    }


def _pair(mutate) -> Any:
    left = _dossier()
    right = deepcopy(left)
    mutate(right)
    return compare(left, right, "local", "gcp")


# --------------------------------------------------------------------------------------- #
# The fixture is the wire, or nothing below this line means anything.
# --------------------------------------------------------------------------------------- #
def _doc1_schemas() -> Any:
    """Doc1's own response models, imported from the sibling working tree rather than copied.

    Copying the field names here would let the two drift apart exactly the way the comparator
    and the wire already did once, silently and for months.
    """
    configured = os.environ.get("DOC1_REPO_PATH", "").strip()
    repo = Path(configured) if configured else _REPO_ROOT.parent / "cdd-sow-research"
    module = repo / "src" / "cdd_sow_research" / "api" / "schemas.py"
    if not module.is_file():
        pytest.skip(f"the sibling cdd-sow-research checkout is not at {repo}")
    sys.path.insert(0, str(repo / "src"))
    try:
        from cdd_sow_research.api import schemas
    except ImportError as exc:  # pragma: no cover - environment, not logic
        pytest.skip(f"cdd-sow-research is present but not importable here: {exc}")
    return schemas


def test_the_fixture_is_the_wire() -> None:
    """Every object in the fixture carries exactly the fields its response model publishes.

    Not a subset and not a superset. A missing field lets the comparator stop reading something
    real; an invented one lets it read something that will always be null in production, which is
    the defect this test exists because of.
    """

    schemas = _doc1_schemas()
    dossier = _dossier()
    alert = deepcopy(ALERT)

    pairs: list[tuple[str, dict[str, Any], Any]] = [
        ("dossier", dossier, schemas.CddCaseResponse),
        ("subject", dossier["subject"], schemas.SubjectModel),
        ("rating", dossier["rating"], schemas.RiskRatingModel),
        ("rating.factors[0]", dossier["rating"]["factors"][0], schemas.RiskFactorModel),
        ("rating.citations[0]", dossier["rating"]["citations"][0], schemas.CitationModel),
        ("sow", dossier["sow"], schemas.SourceOfWealthResponse),
        ("sow.sources[0]", dossier["sow"]["sources"][0], schemas.WealthSourceModel),
        ("screening", dossier["screening"], schemas.ScreeningResultModel),
        ("ownership", dossier["ownership"], schemas.OwnershipSummaryModel),
        ("ownership.owners[0]", dossier["ownership"]["owners"][0], schemas.BeneficialOwnerModel),
        ("alert", alert, schemas.ScreeningAlertModel),
        ("alert.entry", alert["entry"], schemas.WatchlistEntryModel),
    ]
    for path, node, model in pairs:
        assert set(node) == set(model.model_fields), (
            f"{path} does not match {model.__name__}: "
            f"invented {sorted(set(node) - set(model.model_fields))}, "
            f"missing {sorted(set(model.model_fields) - set(node))}"
        )

    # And the values type-check against the real models, so the fixture is a response Doc1 could
    # actually have sent rather than the right keys holding the wrong things.
    dossier["screening"]["alerts"] = [alert]
    schemas.CddCaseResponse.model_validate(dossier)


def test_no_compared_field_is_dead_against_the_wire() -> None:
    """Nothing the comparator extracts may be null for a dossier that carries the answer.

    This is the guard the module lacked. ``sow.amounts`` read ``amount``, ``currency`` and
    ``period`` off every source and got three nulls every time; ``rating.factors`` read ``value``
    and ``band``; ``screening.alerts`` read ``list`` and ``open``. Each looked like a comparison
    and compared nothing, and each was green.
    """

    dossier = _dossier()
    dossier["screening"]["alerts"] = [deepcopy(ALERT)]
    summary = comparable(dossier)

    dead = [key for key, value in summary.items() if value is None]
    assert not dead, f"compared but always null against a real dossier: {dead}"

    assert summary["sow.value_bands"] and all(summary["sow.value_bands"]), (
        "the money is the point of this comparison and it read as empty"
    )
    for extracted in (*summary["rating.factors"], *summary["screening.alerts"]):
        empty = [name for name, value in extracted.items() if value is None]
        assert not empty, f"read off the wire and got nothing: {empty} in {extracted}"


# --------------------------------------------------------------------------------------- #
# What must be caught, and what must not.
# --------------------------------------------------------------------------------------- #
def test_identical_dossiers_agree() -> None:
    report = _pair(lambda d: None)
    assert report.agreed
    assert report.compared, "an agreement over zero fields is the vacuous pass, not a pass"


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        ("the risk band", lambda d: d["rating"].__setitem__("band", "low")),
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
            "a scorecard factor that fired on one profile and not the other",
            lambda d: d["rating"]["factors"][0].__setitem__("present", False),
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
            "how much money a source is worth",
            lambda d: d["sow"]["sources"][0].__setitem__("est_value_band", "USD 10m-50m"),
        ),
        (
            "the CURRENCY the money is in",
            lambda d: d["sow"]["sources"][0].__setitem__("est_value_band", "SGD 1m-5m"),
        ),
        (
            "a source of wealth one profile did not extract",
            lambda d: d["sow"].__setitem__("sources", []),
        ),
        (
            "the screening list version",
            lambda d: d["screening"].__setitem__("lists_version", "2020-01-01"),
        ),
        (
            "an alert that only one profile raised",
            lambda d: d["screening"].__setitem__("alerts", [deepcopy(ALERT)]),
        ),
        (
            "a screen one profile silently did not run",
            lambda d: d.__setitem__("screening", None),
        ),
        (
            "ownership one profile silently did not resolve",
            lambda d: d.__setitem__("ownership", None),
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
            "which watchlist the hit came from",
            lambda a: a["entry"].__setitem__("source", "un"),
        ),
        ("who it matched", lambda a: a.__setitem__("matched_name", "B. Lim")),
        (
            "an alert dispositioned on one profile and still open on the other",
            lambda a: a.__setitem__("status", "CLEARED"),
        ),
    ],
)
def test_alert_divergence_is_caught(name: str, mutate) -> None:
    """An alert is which list, who, and what was decided. All three must be able to fail."""

    left = _dossier()
    left["screening"]["alerts"] = [deepcopy(ALERT)]
    right = deepcopy(left)
    mutate(right["screening"]["alerts"][0])

    report = compare(left, right, "local", "gcp")

    assert not report.agreed, f"{name} diverged and the comparison called it a match"
    assert "screening.alerts" in [d.field for d in report.divergences]


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
            "how a profile classified the wealth it read",
            lambda d: d["sow"]["sources"][0].__setitem__("kind", "other"),
        ),
        (
            "the confidence a profile assigns its own reading",
            lambda d: d["sow"].__setitem__("confidence", 0.42),
        ),
        (
            "the same money written a different way",
            lambda d: d["sow"]["sources"][0].__setitem__(
                "est_value_band", "USD 1,000,000 to USD 5,000,000"
            ),
        ),
        (
            "a factor's explanatory prose",
            lambda d: d["rating"]["factors"][0].__setitem__("detail", "Entity customer, SG."),
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
            "the owners two profiles resolved from two registries",
            lambda d: d["ownership"].__setitem__("owners", []),
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
    for key, reason in ONE_WAY.items():
        assert len(reason) > 20, f"{key} is tolerated in one direction without a stated reason"


# --------------------------------------------------------------------------------------- #
# The money. Formatting may differ; the figure may not.
# --------------------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("USD 1m-5m", "USD 1,000,000 - 5,000,000"),
        ("SGD 10,000,000 to SGD 15,000,000", "sgd 10m-15m"),
        ("USD 100k-1m", "USD  100,000  to  1,000,000 "),
        ("USD 1.5bn-2bn", "USD 1,500,000,000-2,000,000,000"),
    ],
)
def test_one_figure_written_two_ways_is_not_a_divergence(left: str, right: str) -> None:
    assert _value_band(left) == _value_band(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("USD 1m-5m", "SGD 1m-5m"),
        ("USD 1m-5m", "USD 10m-50m"),
        ("USD 1m-5m", "USD 1m-50m"),
        ("USD 1m-5m", ""),
        ("a substantial estate", "a modest estate"),
        # A qualifier changes what the money means, so the canonicaliser may not swallow it.
        ("USD 1m annually", "USD 1m monthly"),
        ("USD 1m", "USD 1m per year"),
    ],
)
def test_two_different_answers_about_the_money_diverge(left: str, right: str) -> None:
    assert _value_band(left) != _value_band(right)


def test_an_unparseable_band_keeps_its_own_text() -> None:
    """Bands the canonicaliser cannot read must not all collapse into one another."""
    assert _value_band("undisclosed") == "UNDISCLOSED"
    assert _value_band("not established") != _value_band("undisclosed")


# --------------------------------------------------------------------------------------- #
# Present versus absent, and the one asymmetry that is tolerated.
# --------------------------------------------------------------------------------------- #
def test_present_versus_absent_is_itself_compared() -> None:
    """A profile that omits ownership must not read as agreeing with one that resolved it."""
    left = _dossier()
    right = deepcopy(left)
    right["ownership"] = None
    report = compare(left, right, "local", "gcp")
    assert not report.agreed
    assert any(d.field == "ownership.present" for d in report.divergences)


def test_the_laptop_not_searching_the_web_is_tolerated_and_recorded() -> None:
    """The declared reduction: no public-web grounding on a laptop, a real search on the cloud."""

    report = _pair(
        lambda d: d.__setitem__(
            "adverse_media",
            {
                "subject_name": "Meridian Harbour Holdings Pte Ltd",
                "findings": [],
                "sources": ["google-search"],
                "searched_at": "2026-08-29T12:06:00+00:00",
            },
        )
    )

    assert report.agreed, "the laptop reporting NOT SEARCHED is the declared reduction"
    assert [t.field for t in report.tolerated] == ["adverse_media.searched"], (
        "a tolerated asymmetry that is not recorded is indistinguishable from one nobody noticed"
    )
    assert report.as_dict()["tolerated_asymmetries"][0]["reason"]


def test_the_managed_profile_dropping_its_search_is_a_divergence() -> None:
    """The other direction is a regression, not a reduction, and this is the point of the key."""

    left = _dossier()
    left["adverse_media"] = {
        "subject_name": "Meridian Harbour Holdings Pte Ltd",
        "findings": [],
        "sources": ["google-search"],
        "searched_at": "2026-08-29T12:06:00+00:00",
    }
    right = deepcopy(left)
    right["adverse_media"] = None

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert any(d.field == "adverse_media.searched" for d in report.divergences)
    assert not report.tolerated


def test_an_empty_dossier_is_a_failure_not_a_match() -> None:
    """``all(())`` is vacuously true. Two empty answers must never pair."""
    with pytest.raises(PairingError):
        compare({}, {}, "local", "gcp")


def test_a_non_object_response_is_refused() -> None:
    with pytest.raises(PairingError):
        comparable(["not", "a", "dossier"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------- #
# A pair is a pair of RUNS. Two files sitting in a directory are not.
# --------------------------------------------------------------------------------------- #
def _write_run(out_dir: Path, dossier: dict[str, Any], *, digest: str | None = None) -> Path:
    """Write one target's artifacts the way ``rm_journey`` writes them."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dossier, indent=2) + "\n"
    (out_dir / "dossier.json").write_text(payload, encoding="utf-8")
    (out_dir / "evidence.json").write_text(
        json.dumps(
            {
                "target": out_dir.name,
                "base_url": "http://localhost:3000",
                "captured_at": "2026-08-29T12:10:00+00:00",
                "steps": [
                    {
                        "step": CAPTURE_STEP,
                        "generated_at": dossier.get("generated_at"),
                        "dossier_sha256": digest
                        or hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_dir


def test_a_run_carries_its_own_provenance(tmp_path: Path) -> None:
    out_dir = _write_run(tmp_path / "local", _dossier())

    dossier, provenance = load_run(out_dir, "local")

    assert dossier["rating"]["band"] == "medium"
    assert provenance["base_url"] == "http://localhost:3000"
    assert provenance["captured_at"] == "2026-08-29T12:10:00+00:00"
    assert provenance["dossier_generated_at"] == "2026-08-29T12:08:00+00:00"


def test_a_missing_dossier_is_not_an_agreeing_side(tmp_path: Path) -> None:
    (tmp_path / "gcp").mkdir()
    with pytest.raises(PairingError, match="missing"):
        load_run(tmp_path / "gcp", "gcp")


def test_a_dossier_with_no_run_record_beside_it_is_refused(tmp_path: Path) -> None:
    out_dir = _write_run(tmp_path / "gcp", _dossier())
    (out_dir / "evidence.json").unlink()
    with pytest.raises(PairingError, match="not evidence of a run"):
        load_run(out_dir, "gcp")


def test_a_stale_dossier_left_by_a_failed_run_is_refused(tmp_path: Path) -> None:
    """The exact 2026-08-29 shape: a failed run, and yesterday's dossier still sitting there.

    ``make e2e-pair`` in that window compared an artifact from a deployment that no longer
    existed and exited zero. The run record beside a dossier now vouches for its bytes.
    """

    out_dir = _write_run(tmp_path / "gcp", _dossier())
    stale = _dossier()
    stale["rating"]["band"] = "low"
    (out_dir / "dossier.json").write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(PairingError, match="different runs"):
        load_run(out_dir, "gcp")


def test_a_run_record_predating_the_digest_is_refused(tmp_path: Path) -> None:
    """An evidence file that cannot vouch for anything is not provenance."""

    out_dir = _write_run(tmp_path / "gcp", _dossier())
    evidence = json.loads((out_dir / "evidence.json").read_text(encoding="utf-8"))
    del evidence["steps"][0]["dossier_sha256"]
    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    with pytest.raises(PairingError, match="no dossier digest"):
        load_run(out_dir, "gcp")


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
        [_citation(title="SoW", page=1), _citation(title="SoW", page=2), _citation(title="SoW")],
    )
    right = _with_citations(_dossier(), [_citation(title="SoW", page=9)])

    report = compare(left, right, "local", "gcp")

    assert report.agreed, [d.field for d in report.divergences]


def test_a_claim_grounded_in_a_different_document_is_a_divergence() -> None:
    """The property re-chunking tolerance must not cost: WHICH documents still matters."""

    left = _with_citations(_dossier(), [_citation(source_id="doc-1", title="SoW")])
    right = _with_citations(_dossier(), [_citation(source_id="doc-2", title="Something else")])

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in [d.field for d in report.divergences]


def test_a_second_distinct_document_is_a_divergence_not_a_chunk() -> None:
    """Grouping by source_id must not let a genuinely extra source hide as a segment."""

    left = _with_citations(_dossier(), [_citation(source_id="doc-1", title="SoW")])
    right = _with_citations(
        _dossier(),
        [_citation(source_id="doc-1", title="SoW"), _citation(source_id="doc-2", title="SoW")],
    )

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in [d.field for d in report.divergences]


def test_a_title_that_decayed_into_its_own_id_is_caught() -> None:
    """The managed defect itself: same document, same count, name lost."""

    left = _with_citations(_dossier(), [_citation(source_id="doc-1", title="Bank statement")])
    right = _with_citations(_dossier(), [_citation(source_id="doc-1", title="doc-1")])

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in {d.field for d in report.divergences}


def test_two_distinct_documents_sharing_a_title_are_not_collapsed() -> None:
    """Titles are compared as a multiset. Two bank statements are two sources."""

    left = _with_citations(
        _dossier(),
        [
            _citation(source_id="doc-1", title="bank_statement"),
            _citation(source_id="doc-2", title="bank_statement"),
        ],
    )
    right = _with_citations(_dossier(), [_citation(source_id="doc-1", title="bank_statement")])

    report = compare(left, right, "local", "gcp")

    assert not report.agreed
    assert "sow.citations" in [d.field for d in report.divergences]
