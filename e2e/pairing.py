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

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The step ``rm_journey`` records when it writes ``dossier.json``. The digest it carries there is
#: what makes a dossier and the run beside it provably the same run.
CAPTURE_STEP = "deterministic artifact captured"

#: Every exemption carries its reason. A list of field names with no reasons beside them becomes,
#: within one refactor, the place a real divergence goes to be forgotten: whoever hits a red pair
#: can silence it by appending a line, and nothing in review distinguishes that from a legitimate
#: reduction. Each entry here names a property of the PROFILE, not a property of a failing run.
EXEMPT: dict[str, str] = {
    "rating.rationale": (
        "prose. The model narrates; it never produces the number. The band and score beside it "
        "are compared, so a rationale that disagrees with them is caught there."
    ),
    "sow.narrative": (
        "prose. The estimated value bands it summarises, whether any source was extracted at "
        "all, and the citations grounding it are all compared."
    ),
    "sow.sources[].description": (
        "prose extracted per source; the estimated value band beside it is compared."
    ),
    "adverse_media findings": (
        "the offline adapter reports NOT SEARCHED by design (no public-web grounding on a laptop) "
        "while the managed one performs a real search. Comparing the findings would demand the "
        "laptop invent a result, which is the exact defect the not-screened/none-found "
        "distinction exists to prevent. The absence is itself correct behaviour on one profile, "
        "**in one direction only**: the REDUCED profile (the left target, the laptop) may report "
        "no search. The managed profile going quiet is a regression, not a reduction, so "
        "`adverse_media.searched` IS compared and only the left-absent/right-present asymmetry "
        "is tolerated. It is recorded in the report when it is tolerated, never dropped in "
        "silence. Narrowed 2026-08-29: this entry used to exempt the whole subtree, and a "
        "managed profile that stopped searching altogether would have paired green."
    ),
    "sow.sources[].kind / sow.confidence": (
        "EXTRACTION quality, which the published claim already permits to differ. Both profiles "
        "read the SAME document and both produce a source-of-wealth breakdown; what differs is "
        "how a frontier model and a laptop model classify the wealth described in one page of "
        "prose -- `asset_sale` and `business_ownership` against `business_ownership` and "
        "`other` -- and what confidence each assigns its own reading. That is the declared "
        "reduction the invariant names as quality, in the same class as the adverse_media and "
        "ownership exemptions above. "
        "**Only the classification and the confidence are exempt.** sow.value_bands, sow.present "
        "and sow.citations stay compared, deliberately, and the first of those is the point: "
        "the estimated value of a source is the consequential figure a reviewer acts on, so "
        "two profiles reading one statement must agree about the money even when they disagree "
        "about what to call it. Exempting the whole subtree would have taken the money with "
        "it -- the over-broad exemption this module exists to refuse, and the first attempt at "
        "this entry did exactly that until a guard in tests/test_pairing.py caught it. "
        "What the wire carries is a BAND and never a spurious precise figure, so the band is "
        "what is compared, canonicalised so that formatting alone cannot read as disagreement. "
        "Until 2026-08-29 this comparison read `amount`, `currency` and `period`, three fields "
        "that exist on no response: it compared None with None on every run and degenerated to "
        "a count of sources. "
        "A profile that extracted NOTHING is still caught by sow.present, and the band and "
        "score are no longer downstream of any of this: since 2026-08-27 they come from the "
        "deterministic scorecard, so how the narrative is classified can no longer move the "
        "consequential number. Decided 2026-08-27."
    ),
    "ownership.owners / ownership.citations": (
        "entity resolution against two different sources of truth, which is a property of the "
        "PROFILE and not of a run. The laptop resolves the demo subject from a seeded registry "
        "fixture; the managed profile asks a grounded web search, which correctly finds nothing "
        "for an entity that does not exist. Comparing them would demand that the managed side "
        "invent an owner for a fictional company, which is the same defect the adverse_media "
        "exemption above exists to prevent, and the citations diverge only because they are "
        "citations OF those two different sources. "
        "**ownership.present and ownership.root_entity stay compared**, deliberately: whether "
        "this profile resolved ownership at all is exactly the kind of silent omission the pair "
        "exists to catch, and exempting the subtree wholesale would have hidden it. Decided "
        "2026-08-26 rather than fixed, because no code change can make a grounded search find a "
        "company that was never incorporated."
    ),
    "rating.factors[].detail": (
        "the one-line prose a factor carries to explain itself. The factor's NAME, its WEIGHT "
        "and whether it is PRESENT -- the three things that make the score what it is -- are all "
        "compared beside it."
    ),
    "screening.alerts[].score / .features": (
        "name-matching internals: how strongly a matcher scored one candidate and which token "
        "features it matched on. WHICH watchlist the hit came from, WHO it matched and the "
        "alert's disposition are compared instead, and those are what a reviewer acts on."
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
    "citations[] passage count": (
        "how many SEGMENTS a store split one document into. Document AI and a local parser "
        "chunk the same page differently, which is the same layout fidelity exempted above. "
        "The count that is compared is of distinct DOCUMENTS, which no re-chunking moves, and "
        "their titles are compared beside it."
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


#: A magnitude with an optional scale suffix: ``1m``, ``100k``, ``1.5bn``, ``12400000``.
_MAGNITUDE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(BN|[KMB])?\b")
_SCALE = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "BN": 1_000_000_000}
_WORD_RE = re.compile(r"[A-Z]+")
#: The only words dropped: the ones that join or hedge the two ends of a range and carry no
#: figure of their own. Everything else a band says is kept, because a qualifier like "annually"
#: changes what the money MEANS and a canonicaliser that swallowed it would hide a real
#: disagreement. This list stays short and stays justified for the same reason EXEMPT does.
_RANGE_WORDS = frozenset(
    {"TO", "AND", "BETWEEN", "APPROX", "APPROXIMATELY", "ABOUT", "CIRCA", "EST", "ESTIMATED"}
)


def _value_band(raw: Any) -> str:
    """Canonicalise a source's estimated value so FORMATTING cannot read as disagreement.

    The wire carries ``est_value_band`` and nothing else: the domain returns "a band, never a
    spurious precise figure", so there is no amount, no currency field and no period to compare,
    and a comparison that reads those three compares nothing at all. What a reviewer acts on is
    the money, so the money is what must agree, and the band is the only form of it that exists.

    ``USD 1m-5m`` and ``USD 1,000,000 - 5,000,000`` are one figure written twice and must not
    diverge. ``USD 1m-5m`` and ``SGD 10,000,000 to SGD 15,000,000`` are two different answers
    about how much money there is, and must. So every magnitude is expanded to digits and every
    remaining word is kept beside it, minus the range connectives above.

    Keeping the words rather than picking out a currency code is deliberate. A three-letter token
    is not reliably a currency, and anything this function DROPS it can no longer see: "USD 1m
    annually" and "USD 1m monthly" are not the same claim about the money.

    A band this cannot parse falls back to its own normalised text rather than to a constant.
    Collapsing unparseable bands to one value would make every one of them agree with every
    other, which is the vacuous pass in a different costume.
    """

    text = " ".join(str(raw or "").split()).upper().replace(",", "")
    if not text:
        return ""

    magnitudes: list[str] = []

    def _expand(match: re.Match[str]) -> str:
        value = float(match.group(1)) * _SCALE[match.group(2) or ""]
        magnitudes.append(str(int(value)) if value.is_integer() else str(value))
        return " "

    remainder = _MAGNITUDE_RE.sub(_expand, text)
    if not magnitudes:
        return text
    words = sorted({w for w in _WORD_RE.findall(remainder) if w not in _RANGE_WORDS})
    return " ".join([*words, "-".join(magnitudes)])


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
    # Group by source_id FIRST. A store that returns one document as eight extractive
    # segments and a store that returns it as three yields eight citations against three,
    # and comparing those raw counts measures the CHUNKER -- parser fidelity, which is
    # already a declared reduction two entries down. What must agree is which documents
    # ground the claim, and that is one entry per document however it was split.
    by_source: dict[str, list[dict[str, Any]]] = {}
    for c in cits:
        by_source.setdefault(str(c.get("source_id") or ""), []).append(c)
    return {
        # Distinct documents, not passages. This is the "how many sources ground this
        # claim" the docstring promises, and it is stable under re-chunking.
        "count": len(by_source),
        "types": sorted({str(c.get("source_type")) for c in cits}),
        # WHICH documents, by the only identity that crosses profiles. Compared as a sorted
        # multiset so two distinct bank statements do not collapse into one. Titles were not
        # compared at all before, which is how eight citations that had all decayed into
        # their own ids still reported a difference only in the count.
        "titles": sorted(str(group[0].get("title") or "") for group in by_source.values()),
        # A citation whose title is merely its own id is not a usable evidence link. Counting
        # them is deliberate: it is the difference between "grounded in the bank statement" and
        # "grounded in doc-c9dba9861a1f", and a profile that loses the human-readable source
        # name has degraded the evidence relationship even though the link still resolves.
        "opaque_titles": sum(
            1
            for group in by_source.values()
            if str(group[0].get("title") or "") == str(group[0].get("source_id") or "")
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
        # A scorecard factor is three things on the wire: its NAME, its WEIGHT, and whether it
        # is PRESENT. ``present`` is the factor's outcome -- the bit that decides whether the
        # weight is applied -- and leaving it out let a factor that fired on one profile and not
        # on the other compare equal, caught only if it happened to move the score. Two
        # compensating flips of equal weight would not have moved it.
        "rating.factors": sorted(
            (
                {
                    "name": f.get("name"),
                    "weight": f.get("weight"),
                    "present": f.get("present"),
                }
                for f in rating.get("factors") or []
            ),
            key=lambda f: str(f["name"]),
        ),
        # The KIND a source is classified as, and the confidence a profile assigns its own
        # reading, are EXEMPT (see EXEMPT above). The MONEY is not: what each source is worth is
        # consequential, it is what a reviewer acts on, and two profiles reading one statement
        # must agree about it even when they disagree about what to call it. Exempting the whole
        # subtree would have taken the money with it, which is the over-broad exemption this
        # module exists to refuse. ``est_value_band`` is the only form of the money the wire
        # carries; see _value_band for why it is canonicalised rather than compared raw.
        "sow.value_bands": sorted(
            _value_band(s.get("est_value_band")) for s in sow.get("sources") or []
        ),
        # And the question exempting a subtree would otherwise bury: did this profile extract a
        # source of wealth AT ALL. A laptop that silently returned nothing and a deployment
        # that returned two sources must still be caught disagreeing.
        "sow.present": bool(sow.get("sources")),
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
        # An alert is WHICH list matched, WHO it matched, and what has been decided about it.
        # The list identity lives on the matched watchlist ENTRY (``entry.source``) and the
        # disposition in ``status``; a comparison reading ``list`` and ``open`` off the alert
        # itself read two fields the wire has never sent, so an OFAC hit on one profile and a UN
        # hit on the other, or a PENDING alert against a dispositioned one, compared as equal.
        out["screening.alerts"] = sorted(
            (
                {
                    "list": (a.get("entry") or {}).get("source"),
                    "matched_name": a.get("matched_name"),
                    "status": a.get("status"),
                }
                for a in screening.get("alerts") or []
            ),
            key=lambda a: (str(a["list"]), str(a["matched_name"]), str(a["status"])),
        )

    # ``owners`` and ``citations`` are exempt (see EXEMPT): the two profiles resolve ownership
    # against different sources of truth by design. Presence and the root entity are NOT, because
    # a profile that quietly stopped resolving ownership would otherwise read as agreement.
    out["ownership.present"] = ownership is not None
    if ownership is not None:
        out["ownership.root_entity"] = ownership.get("root_entity")

    # Whether a public-web search happened AT ALL, on the same present-vs-absent principle. The
    # findings are exempt (see EXEMPT) because the laptop has no grounding to search with, but
    # the exemption runs one way: compare() tolerates the reduced profile being the silent one
    # and reports the reverse. Emitting no key at all, which is what this module did until
    # 2026-08-29, tolerated both directions and nobody had decided that.
    out["adverse_media.searched"] = dossier.get("adverse_media") is not None

    return out


#: Fields whose divergence is tolerated in ONE direction, with the reason, and only ever
#: RECORDED rather than dropped. The convention the direction rests on: the LEFT target is the
#: reduced profile (``pair_report``'s ``--left`` defaults to the laptop for exactly this reason),
#: so the left side may be the one that did not do the work. The right side going quiet is a
#: regression and is reported as a divergence like any other.
ONE_WAY: dict[str, str] = {
    "adverse_media.searched": (
        "the laptop has no public-web grounding and reports NOT SEARCHED by design, so the left "
        "profile may be the one that did not search. The managed profile silently dropping the "
        "search is a regression, not a declared reduction, and diverges."
    ),
}


@dataclass
class Divergence:
    field: str
    left: Any
    right: Any


@dataclass
class ToleratedAsymmetry:
    """A one-directional divergence the pair permits. Recorded, so nobody has to guess it ran."""

    field: str
    left: Any
    right: Any
    reason: str


@dataclass
class Report:
    left_target: str
    right_target: str
    compared: list[str] = field(default_factory=list)
    divergences: list[Divergence] = field(default_factory=list)
    tolerated: list[ToleratedAsymmetry] = field(default_factory=list)
    #: Where each side came from and when, filled in by whoever loaded the artifacts. A
    #: comparison with no provenance cannot tell a fresh pair from a pair of stale files.
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def agreed(self) -> bool:
        return not self.divergences

    def as_dict(self) -> dict[str, Any]:
        return {
            "left": self.left_target,
            "right": self.right_target,
            "agreed": self.agreed,
            "provenance": self.provenance,
            "compared_fields": self.compared,
            "divergences": [
                {"field": d.field, self.left_target: d.left, self.right_target: d.right}
                for d in self.divergences
            ],
            "tolerated_asymmetries": [
                {
                    "field": t.field,
                    self.left_target: t.left,
                    self.right_target: t.right,
                    "reason": t.reason,
                }
                for t in self.tolerated
            ],
            "declared_reductions": EXEMPT,
        }


def compare(left: dict[str, Any], right: dict[str, Any], left_name: str, right_name: str) -> Report:
    """Diff the comparable halves of two dossiers.

    ``left`` is the REDUCED profile and ``right`` the managed one. The order is not cosmetic: the
    entries in ``ONE_WAY`` are tolerated in the left-to-right direction only, so swapping the two
    would tolerate a managed regression.

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
        left_value, right_value = a.get(key), b.get(key)
        if left_value == right_value:
            continue
        reason = ONE_WAY.get(key)
        if reason is not None and left_value is False and right_value is True:
            report.tolerated.append(
                ToleratedAsymmetry(field=key, left=left_value, right=right_value, reason=reason)
            )
            continue
        report.divergences.append(Divergence(field=key, left=left_value, right=right_value))
    return report


def load_run(out_dir: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one side of the pair, refusing an artifact that is not from the run beside it.

    A dossier alone says nothing about WHEN or WHERE it came from, and a failed run used to leave
    the previous run's dossier sitting in place: the pair then reported a PASS for a run that
    never happened, which on 2026-08-29 would have meant a PASS against a deployment that had
    been deleted. ``rm_journey`` now clears both files before it starts and records the dossier's
    digest in the evidence beside it, so the two are provably one run or the pair is refused.

    A missing side is not an agreeing side, and a stale side is worse: it looks like one.
    """

    dossier_path = out_dir / "dossier.json"
    evidence_path = out_dir / "evidence.json"
    if not dossier_path.is_file():
        raise PairingError(
            f"{dossier_path} is missing. Run the journey against {name} first; a missing side is "
            f"not an agreeing side."
        )
    if not evidence_path.is_file():
        raise PairingError(
            f"{evidence_path} is missing, so nothing records which run produced "
            f"{dossier_path}. An artifact with no run record beside it is not evidence of a run."
        )

    raw = dossier_path.read_text(encoding="utf-8")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    steps = evidence.get("steps") or []
    captured = next((s for s in steps if s.get("step") == CAPTURE_STEP), None)
    recorded = (captured or {}).get("dossier_sha256")
    if not recorded:
        raise PairingError(
            f"{evidence_path} records no dossier digest, so it cannot vouch for "
            f"{dossier_path.name}. Re-run the journey against {name}."
        )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if digest != recorded:
        raise PairingError(
            f"{dossier_path} is not the artifact the run beside it captured (the run recorded "
            f"{recorded[:12]}, the file on disk is {digest[:12]}). The two are from different "
            f"runs, so this side is stale. Re-run the journey against {name}."
        )

    dossier = json.loads(raw)
    provenance = {
        "base_url": evidence.get("base_url"),
        "captured_at": evidence.get("captured_at"),
        "dossier_generated_at": captured.get("generated_at") if captured else None,
        "dossier_sha256": digest,
    }
    return dossier, provenance
