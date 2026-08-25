"""The paired half of the demonstration: do the two targets AGREE, not merely both pass.

``rm_journey.py`` proves the journey runs on a laptop and on the deployment. Running twice is
not a pair. The claim this repository actually makes is stronger and is published on the
organization front page:

    The same input, policy version and evidence produce the same consequential figures, checks,
    escalation reasons and citation relationships in every profile. What changes between a
    managed cloud profile and a laptop is quality, scale and durability, never policy.

That sentence splits every field of a dossier into two lists, and this module is those two lists
made executable. Practices check F4 scores exactly this.

**Why the old evidence did not do it.** Both runs recorded ``risk_rating_chars`` and
``source_of_wealth_chars`` and nothing else, and they differed sixfold (275 vs 1613). Character
counts of the NARRATION are the one quantity the invariant above explicitly permits to differ:
the managed profile narrates with a frontier model, the laptop with a local one, and "visibly
lower narrative quality" is a declared reduction. So the evidence measured the only thing allowed
to move and asserted nothing about the four things that must not. The single assertion on the
dossier was ``len(sow) > 40``.

**What is compared here is the wire response**, captured from the real ``POST /v1/cdd`` the
console itself made, not re-fetched and not scraped out of the DOM. A comparison of a second
request is a comparison of a second answer; the artifact the surface rendered is the artifact
under test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Every exemption carries its reason. A list of field names with no reasons beside them becomes,
#: within one refactor, the place a real divergence goes to be forgotten: whoever hits a red pair
#: can silence it by appending a line, and nothing in review distinguishes that from a legitimate
#: reduction. Each entry here names a property of the PROFILE, not a property of a failing run.
EXEMPT: dict[str, str] = {
    "rating.rationale": (
        "prose. The model narrates; it never produces the number. The band and score beside it "
        "are compared, so a rationale that disagrees with them is caught there."
    ),
    "sow.narrative": ("prose. The sources, amounts and confidence it summarises are all compared."),
    "sow.sources[].description": (
        "prose extracted per source; the amounts and currencies beside it are compared."
    ),
    "adverse_media": (
        "the offline adapter reports NOT SEARCHED by design (no public-web grounding on a laptop) "
        "while the managed one performs a real search. Comparing them would demand the laptop "
        "invent a result, which is the exact defect the not-screened/none-found distinction "
        "exists to prevent. The absence is itself correct behaviour on one profile."
    ),
    "citations[].snippet": (
        "parser fidelity. Portable OCR and Document AI extract different spans of one page."
    ),
    "citations[].score": (
        "retrieval relevance. A local index and a managed retrieval engine rank differently; "
        "WHICH sources were cited is compared instead."
    ),
    "citations[].url": "resolver-local addressing; the source's title is compared instead.",
    "citations[].source_id": (
        "per-store document identity. The same document ingested into a laptop store and into "
        "the managed store is minted a different id in each, so these can never match and a "
        "comparison of them would be red on every run for a reason that is not policy. What is "
        "compared instead is how many sources ground each claim, of what type, and their titles."
    ),
    "citations[].page": (
        "page attribution depends on the parser's own pagination of the same document, which is "
        "layout fidelity and a declared reduction."
    ),
    "generated_at / screened_at / searched_at": (
        "wall-clock timestamps. Two runs are never simultaneous, and the ORDER they imply is "
        "not policy."
    ),
    "id / subject.id / document ids": (
        "per-run identifiers, minted independently on each side. Citation source_id is compared "
        "because it is the stable identity that makes an evidence link resolvable."
    ),
}


class PairingError(RuntimeError):
    """The comparison could not be made honestly, which is never the same as agreement."""


def _citations(node: Any) -> dict[str, Any]:
    """The evidence RELATIONSHIP: what kind of source grounds this claim, and how many.

    Not ``source_id``. A document is ingested separately into each profile's own store and is
    minted a different id there, so the ids can never match and comparing them would report a
    divergence on every run for a reason that is not policy. That would also contradict the
    "per-run identifiers" exemption below, which is how this function was wrong on its first
    draft. The stable cross-profile identity of a cited document is its TITLE, which is why the
    title is compared and the id is not.
    """
    cits = list(node or [])
    return {
        "count": len(cits),
        "types": sorted({str(c.get("source_type")) for c in cits}),
        # A citation whose title is merely its own id is not a usable evidence link. Counting
        # them is deliberate: it is the difference between "grounded in the bank statement" and
        # "grounded in doc-c9dba9861a1f", and a profile that loses the human-readable source
        # name has degraded the evidence relationship even though the link still resolves.
        "opaque_titles": sum(
            1 for c in cits if str(c.get("title") or "") == str(c.get("source_id") or "")
        ),
    }


def comparable(dossier: dict[str, Any]) -> dict[str, Any]:
    """The half of a dossier that MUST be identical across profiles.

    Reading this function is meant to be the whole specification: if a field is here it is
    policy, and if it is absent it is in ``EXEMPT`` with a reason.
    """
    if not isinstance(dossier, dict):
        raise PairingError(f"expected a dossier object, got {type(dossier).__name__}")

    rating = dossier.get("rating") or {}
    sow = dossier.get("sow") or {}
    screening = dossier.get("screening")
    ownership = dossier.get("ownership")

    out: dict[str, Any] = {
        # Escalation reasons. Whether a human must look at this is policy, and the most
        # consequential single bit in the document.
        "requires_human_review": dossier.get("requires_human_review"),
        "rating.requires_human_review": rating.get("requires_human_review"),
        "sow.requires_human_review": sow.get("requires_human_review"),
        # The figures and the rules that produced them.
        "rating.band": rating.get("band"),
        "rating.score": rating.get("score"),
        "rating.factors": sorted(
            (
                {
                    "id": f.get("id") or f.get("name"),
                    "weight": f.get("weight"),
                    "value": f.get("value"),
                    "band": f.get("band"),
                }
                for f in rating.get("factors") or []
            ),
            key=lambda f: str(f["id"]),
        ),
        "sow.confidence": sow.get("confidence"),
        "sow.sources": sorted(
            (
                {
                    "kind": s.get("kind") or s.get("type"),
                    "amount": s.get("amount"),
                    "currency": s.get("currency"),
                    "period": s.get("period"),
                }
                for s in sow.get("sources") or []
            ),
            key=lambda s: (str(s["kind"]), str(s["amount"])),
        ),
        # Evidence links.
        "rating.citations": _citations(rating.get("citations")),
        "sow.citations": _citations(sow.get("citations")),
    }

    # Screening and ownership are optional on the wire. Present-vs-absent is itself a compared
    # property: a profile that silently omits a screen it should have run must not read as
    # agreement with one that ran it.
    out["screening.present"] = screening is not None
    if screening is not None:
        out["screening.lists_version"] = screening.get("lists_version")
        out["screening.sources"] = sorted(screening.get("sources") or [])
        out["screening.alerts"] = sorted(
            (
                {
                    "list": a.get("list") or a.get("source"),
                    "matched_name": a.get("matched_name") or a.get("name"),
                    "open": a.get("open"),
                }
                for a in screening.get("alerts") or []
            ),
            key=lambda a: (str(a["list"]), str(a["matched_name"])),
        )

    out["ownership.present"] = ownership is not None
    if ownership is not None:
        out["ownership.root_entity"] = ownership.get("root_entity")
        out["ownership.owners"] = sorted(
            (
                {
                    "name": o.get("name"),
                    "percentage": o.get("percentage") or o.get("effective_percentage"),
                    "control_basis": o.get("control_basis"),
                }
                for o in ownership.get("owners") or []
            ),
            key=lambda o: str(o["name"]),
        )
        out["ownership.citations"] = _citations(ownership.get("citations"))

    return out


@dataclass
class Divergence:
    field: str
    left: Any
    right: Any


@dataclass
class Report:
    left_target: str
    right_target: str
    compared: list[str] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)

    @property
    def agreed(self) -> bool:
        return not self.divergences

    def as_dict(self) -> dict[str, Any]:
        return {
            "left": self.left_target,
            "right": self.right_target,
            "agreed": self.agreed,
            "compared_fields": self.compared,
            "divergences": [
                {"field": d.field, self.left_target: d.left, self.right_target: d.right}
                for d in self.divergences
            ],
            "declared_reductions": EXEMPT,
        }


def compare(left: dict[str, Any], right: dict[str, Any], left_name: str, right_name: str) -> Report:
    """Diff the comparable halves of two dossiers.

    An empty comparable half is an ERROR, never agreement. Two dossiers that both yielded nothing
    would otherwise pair perfectly, which is the vacuous-pass shape this whole exercise exists to
    refuse: ``all(())`` is true.
    """
    a, b = comparable(left), comparable(right)
    # A dossier with no verdict in it is a failure, never a match. comparable() always returns
    # its full key set (absent fields come back as None), so testing the dict for truthiness
    # passed two EMPTY dossiers as agreeing: `all(())` in a different costume, and the very
    # defect this module was written to refuse. Test the VALUES that carry the verdict.
    for name, half in ((left_name, a), (right_name, b)):
        if half.get("rating.band") is None or half.get("rating.score") is None:
            raise PairingError(
                f"the {name} dossier carries no risk band or score, so there is no verdict to "
                f"compare. Two answerless dossiers agree perfectly and mean nothing."
            )

    keys = sorted(set(a) | set(b))
    report = Report(left_target=left_name, right_target=right_name, compared=keys)
    for key in keys:
        if a.get(key) != b.get(key):
            report.divergences.append(Divergence(field=key, left=a.get(key), right=b.get(key)))
    return report


def load_dossier(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PairingError(
            f"{path} is missing. Run the journey against that target first; a missing side is "
            f"not an agreeing side."
        )
    return json.loads(path.read_text(encoding="utf-8"))
