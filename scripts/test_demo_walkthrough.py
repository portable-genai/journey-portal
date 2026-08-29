#!/usr/bin/env python3
"""Offline coverage for walkthrough selection and rehearsal output."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import unittest
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).with_name("demo_walkthrough.py")
_SPEC = importlib.util.spec_from_file_location("demo_walkthrough", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
walkthrough = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = walkthrough
_SPEC.loader.exec_module(walkthrough)

# A label-and-colon heading, the shape the notes deliberately no longer use.
_LABEL_HEADING = re.compile(r"^[A-Z][A-Z ]{3,}:")


class _FakeLocator:
    def wait_for(self, *args: object, **kwargs: object) -> None:
        pass


class _FakePage:
    """A minimal Playwright page stand-in: reports one Doc1 healthz profile."""

    def __init__(self, profile: str) -> None:
        self._profile = profile
        self.consulted = False

    def goto(self, *args: object, **kwargs: object) -> None:
        self.consulted = True

    def get_by_role(self, *args: object, **kwargs: object) -> _FakeLocator:
        return _FakeLocator()

    def evaluate(self, script: str, *args: object) -> dict[str, str]:
        self.consulted = True
        return {"profile": self._profile}


class _Timeout(RuntimeError):
    """Stands in for the timeout Playwright raises when a wait never resolves."""


class _El:
    """One rendered element: the exact text it puts in the document, and how it is reached.

    ``rule_id`` is the rule name a findings row carries beside its status badge, which the
    marketing-gate step reaches from the badge itself.
    """

    def __init__(self, text: str, *, role: str = "", tag: str = "", rule_id: str = "") -> None:
        self.text = text
        self.role = role
        self.tag = tag
        self.rule_id = rule_id


class _FakeConsole:
    """A console stand-in that renders one screen before a submission and another after.

    It models the two matching rules the walkthrough's assertions actually depend on: a text
    match is a case-INSENSITIVE substring of an element's own text unless ``exact`` is set, in
    which case it is a case-sensitive whole-string match; a role match is against the element's
    role with a case-insensitive whole-string accessible name. Screens are given as the strings
    the real components render, read off those components.
    """

    def __init__(self, before: list[_El], after: list[_El]) -> None:
        self._rendered = list(before)
        self._after = list(after)
        self.submitted = False

    def submit(self) -> None:
        self.submitted = True
        self._rendered = list(self._after)

    def _loc(self, matches: list[_El], label: str) -> _FakeElementList:
        return _FakeElementList(self, matches, label)

    def get_by_text(self, text: Any, exact: bool = False) -> _FakeElementList:
        if isinstance(text, re.Pattern):
            hits = [el for el in self._rendered if text.search(el.text)]
            return self._loc(hits, f"text matching {text.pattern!r}")
        if exact:
            hits = [el for el in self._rendered if el.text.strip() == text]
            return self._loc(hits, f"the exact text {text!r}")
        needle = text.lower()
        hits = [el for el in self._rendered if needle in el.text.lower()]
        return self._loc(hits, f"text containing {text!r}")

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> Any:
        hits = [
            el
            for el in self._rendered
            if el.role == role
            and (
                name is None
                or (el.text.strip() == name if exact else el.text.strip().lower() == name.lower())
            )
        ]
        return self._loc(hits, f"a {role} named {name!r}")

    def locator(self, selector: str) -> _FakeElementList:
        hits = [el for el in self._rendered if el.tag == selector]
        return self._loc(hits, f"the element {selector!r}")

    def get_by_placeholder(self, text: str, exact: bool = False) -> _FakeElementList:
        return self._loc([], f"a placeholder {text!r}")


class _FakeElementList:
    def __init__(self, console: _FakeConsole, matches: list[_El], label: str) -> None:
        self._console = console
        self._matches = matches
        self._label = label

    @property
    def first(self) -> _FakeElementList:
        return self

    def nth(self, index: int) -> _FakeElementList:
        chosen = self._matches[index : index + 1]
        return _FakeElementList(self._console, chosen, f"{self._label} at {index}")

    def count(self) -> int:
        return len(self._matches)

    def wait_for(self, **_kwargs: object) -> None:
        if not self._matches:
            raise _Timeout(f"waiting for {self._label} never resolved")

    def fill(self, _value: str) -> None:
        self.wait_for()

    def click(self) -> None:
        self.wait_for()
        self._console.submit()

    def locator(self, selector: str) -> _FakeElementList:
        if selector != "xpath=../div/b":
            raise AssertionError(f"the fake console does not model {selector!r}")
        named = [_El(el.rule_id) for el in self._matches if el.rule_id]
        return _FakeElementList(self._console, named, "the rule the failing finding names")


def _run_step(action: Any, console: _FakeConsole) -> None:
    """Drive one walkthrough step against a fake console, with its profile check stubbed."""
    from unittest import mock

    with (
        mock.patch.object(walkthrough, "_require_profile", lambda *a, **k: None),
        mock.patch.object(walkthrough, "_select_journey_tab", lambda *a, **k: console),
    ):
        action(_FakePage(profile="local"))


class StepAssertionTests(unittest.TestCase):
    """Every step that narrates an outcome must fail when the outcome is the other one.

    These are the demonstration's negative controls, and each of them was once satisfied by
    text the console renders either way, so the run could not fail. The pairs below render the
    screens the real components produce and require the step to pass on one and raise on the
    other; a step that cannot raise here cannot fail in the room.
    """

    def test_the_marketing_gate_step_fails_when_the_gate_approves(self) -> None:
        composer = [_El("", tag="textarea"), _El("Run compliance review", role="button")]
        shared = [
            _El("Compliance review", role="heading"),
            _El("Findings (deterministic rule engine)", role="heading"),
            _El("Cited rules", role="heading"),
            _El("Risk-warning requirement (SG)"),
            _El("An investment promotion must carry an approved risk-warning field."),
        ]
        refusal = [
            *shared,
            _El("Non-compliant"),
            _El("FAIL", rule_id="SG-BANK-NO-GUARANTEE"),
            _El("SG-BANK-NO-GUARANTEE"),
        ]
        approval = [
            *shared,
            _El("Compliant"),
            _El("PASS", rule_id="SG-BANK-NO-GUARANTEE"),
            _El("SG-BANK-NO-GUARANTEE"),
        ]

        # The refusal the step is written for.
        _run_step(walkthrough._mkt_gate_refused, _FakeConsole(composer, refusal))

        # The screen a wrongly-approving gate renders still satisfies BOTH of the waits this
        # step used to make, which is exactly why they were replaced.
        rendered_approval = _FakeConsole(approval, approval)
        self.assertTrue(rendered_approval.get_by_text("Cited rules", exact=False).count())
        self.assertTrue(rendered_approval.get_by_text("risk-warning", exact=False).count())
        with self.assertRaises(_Timeout) as raised:
            _run_step(walkthrough._mkt_gate_refused, _FakeConsole(composer, approval))
        self.assertIn("Non-compliant", str(raised.exception))

    def test_the_marketing_gate_step_fails_when_no_finding_names_its_rule(self) -> None:
        # A refusal that names no rule is the case the org run sheet promises will fail.
        unnamed = _FakeConsole(
            [_El("", tag="textarea"), _El("Run compliance review", role="button")],
            [_El("Non-compliant"), _El("FAIL"), _El("Cited rules", role="heading")],
        )
        with self.assertRaises(_Timeout) as raised:
            _run_step(walkthrough._mkt_gate_refused, unnamed)
        self.assertIn("rule the failing finding names", str(raised.exception))

    def test_the_creative_step_fails_on_a_warned_variant(self) -> None:
        inputs = [
            _El("", tag="input"),
            _El("", tag="input"),
            _El("Generate brand-safe creative", role="button"),
        ]
        summary = _El("3/3 variant(s) passed every deterministic check.")
        clean = _FakeConsole(inputs, [summary, _El("PASS"), _El("PASS"), _El("PASS")])

        _run_step(walkthrough._mkt_creative, clean)

        warned = _FakeConsole(inputs, [summary, _El("PASS"), _El("WARN"), _El("PASS")])
        with self.assertRaisesRegex(RuntimeError, "brand warning"):
            _run_step(walkthrough._mkt_creative, warned)

    def test_the_performance_step_fails_when_no_report_is_built(self) -> None:
        # The sidebar lists what the console can report, on every page load and forever.
        sidebar = _El(
            "Cited performance reports (attribution, ROAS / CAC, budget plan, A/B "
            "significance, anomalies), generic across banking and online retail."
        )
        before = [sidebar, _El("", tag="input"), _El("Build cited report", role="button")]
        built = _FakeConsole(
            before,
            [
                sidebar,
                _El("A/B significance", role="heading"),
                _El("Anomalies", role="heading"),
            ],
        )

        _run_step(walkthrough._mkt_performance, built)

        nothing = _FakeConsole(before, [sidebar])
        # The sidebar alone satisfied both of the waits this step used to make.
        self.assertTrue(nothing.get_by_text("A/B significance", exact=False).count())
        self.assertTrue(nothing.get_by_text("Anomalies", exact=False).count())
        with self.assertRaises(_Timeout) as raised:
            _run_step(walkthrough._mkt_performance, nothing)
        self.assertIn("A/B significance", str(raised.exception))

    def test_the_architecture_step_fails_when_validation_returns_nothing(self) -> None:
        waiting = _El(
            "Submit a project on the left to validate it against the 12 General Principles."
        )
        before = [waiting, _El("Validate at intake", role="button")]
        validated = _FakeConsole(
            before, [_El("Principle findings", role="heading"), _El("P-06"), _El("FAIL")]
        )

        _run_step(walkthrough._gov_architecture, validated)

        nothing = _FakeConsole(before, [waiting])
        # The waiting message alone satisfied the wait this step used to make.
        self.assertTrue(nothing.get_by_text("General Principle", exact=False).count())
        with self.assertRaises(_Timeout) as raised:
            _run_step(walkthrough._gov_architecture, nothing)
        self.assertIn("Principle findings", str(raised.exception))

    def test_the_promotion_gate_step_fails_when_no_probe_was_blocked(self) -> None:
        before = [_El("Run promotion gate", role="button")]
        verdict = [
            _El("RED-TEAM REPORT"),
            _El("PROMOTION GATE VERDICT"),
            _El("eval passed but this is not attested promotion evidence"),
        ]
        blocked = _FakeConsole(before, [*verdict, _El("blocked"), _El("not blocked")])

        _run_step(walkthrough._gov_promotion_gate, blocked)

        nothing_blocked = [*verdict, _El("not blocked"), _El("not blocked")]
        # A report in which nothing was blocked satisfied the wait this step used to make.
        rendered = _FakeConsole(nothing_blocked, nothing_blocked)
        self.assertTrue(rendered.get_by_text("BLOCKED", exact=False).count())
        with self.assertRaises(_Timeout) as raised:
            _run_step(walkthrough._gov_promotion_gate, _FakeConsole(before, nothing_blocked))
        self.assertIn("'blocked'", str(raised.exception))


class WalkthroughSelectionTests(unittest.TestCase):
    def test_rm_script_is_the_expected_sequence(self) -> None:
        self.assertEqual(
            [step.id for step in walkthrough.selected_steps("rm")],
            [
                "rm-open",
                "rm-whoami",
                "rm-spoof-rejected",
                "rm-doc1-cdd",
                "rm-doc1-flagged",
                "rm-doc1-blocked",
                "rm-doc3-briefing",
                "rm-switch-approver",
                "close",
            ],
        )

    def test_resume_is_inclusive_and_rejects_another_journeys_step(self) -> None:
        self.assertEqual(
            [step.id for step in walkthrough.selected_steps("ops", "ops-doc4-ucp600")],
            [
                "ops-doc4-ucp600",
                "ops-rsk1-compliance",
                "ops-hrz7-self-approval",
                "ops-hrz7-review",
                "close",
            ],
        )
        with self.assertRaisesRegex(ValueError, "unknown or excluded step"):
            walkthrough.selected_steps("rm", "ops-open")

    def test_select_label_patterns_match_the_real_wrapping_label_text(self) -> None:
        """Doc1 wraps each select in its label, so the label text carries every option.

        These are the exact strings the running Doc1 form produces. An exact match on the
        visible caption ("Type") matches neither, which is what silently broke the CDD
        step; the anchored patterns match their own select and not the other one.
        """
        type_label = "Typeentityindividual"
        doc_type_label = (
            "Document type\nBank statementFinancial statement\nRegistry extractPassport / IDOther\n"
        )

        self.assertRegex(type_label, walkthrough._TYPE_LABEL)
        self.assertRegex(doc_type_label, walkthrough._DOC_TYPE_LABEL)
        # The anchor is what keeps them apart: "Document type" also contains "Type".
        self.assertNotRegex(doc_type_label, walkthrough._TYPE_LABEL)
        self.assertNotRegex(type_label, walkthrough._DOC_TYPE_LABEL)
        # Forward-compatible: a future clean label ("Type") still matches.
        self.assertRegex("Type", walkthrough._TYPE_LABEL)
        self.assertRegex("Document type", walkthrough._DOC_TYPE_LABEL)

    def test_every_application_step_requires_its_live_profile(self) -> None:
        live = {step.id: step.requires_live for step in walkthrough.STEPS if step.requires_live}
        self.assertEqual(
            live,
            {
                "rm-doc1-cdd": ("doc1",),
                "rm-doc1-flagged": ("doc1",),
                "rm-doc1-blocked": ("doc1",),
                "rm-doc3-briefing": ("doc3",),
                "ops-doc2-credit-memo": ("doc2",),
                "ops-doc4-ucp600": ("doc4",),
                "ops-rsk1-compliance": ("rsk1",),
            },
        )
        for step in walkthrough.STEPS:
            for app_id in step.requires_live:
                self.assertIn(app_id, walkthrough._APP_ORIGINS)

    def test_preflight_fails_before_any_step_when_doc1_is_not_live(self) -> None:
        from unittest import mock

        page = _FakePage(profile="local")
        # The packs may or may not be built in this environment; the profile is what this
        # asserts, so stub the pack check to isolate it.
        with (
            mock.patch.object(walkthrough, "_doc1_manifest", lambda: {}),
            self.assertRaisesRegex(RuntimeError, r"profile 'local'"),
        ):
            walkthrough._preflight(page, walkthrough.selected_steps("rm"))

    def test_preflight_passes_when_doc1_is_live(self) -> None:
        from unittest import mock

        page = _FakePage(profile="live")
        with mock.patch.object(walkthrough, "_doc1_manifest", lambda: {}):
            walkthrough._preflight(page, walkthrough.selected_steps("rm"))  # must not raise

    def test_preflight_reports_missing_packs(self) -> None:
        from unittest import mock

        page = _FakePage(profile="live")
        missing = mock.Mock(side_effect=RuntimeError("packs missing: run build_demo_pack.py"))
        with (
            mock.patch.object(walkthrough, "_doc1_manifest", missing),
            self.assertRaisesRegex(RuntimeError, r"packs missing"),
        ):
            walkthrough._preflight(page, walkthrough.selected_steps("rm"))

    def test_preflight_is_skipped_and_touches_nothing_without_a_live_step(self) -> None:
        # Resuming past every application step needs no live profile at all: the
        # preflight must return without consulting the page.
        page = _FakePage(profile="local")
        steps = walkthrough.selected_steps("ops", "ops-hrz7-self-approval")
        walkthrough._preflight(page, steps)
        self.assertFalse(page.consulted, "a resume past the live steps must not be gated")

    def test_preflight_gates_an_ops_run_on_its_apps_live_profiles(self) -> None:
        # An Ops run carries Doc2/Doc4/Rsk1 steps, which run on real or audience data:
        # a non-live stack must fail before the first step.
        page = _FakePage(profile="local")
        with self.assertRaisesRegex(RuntimeError, r"profile 'local'"):
            walkthrough._preflight(page, walkthrough.selected_steps("ops"))

    def test_every_control_is_shown_deciding_both_ways(self) -> None:
        """Each negative control sits directly beside the positive case it discriminates from.

        A demo that only ever shows a control allowing things cannot be told apart from one
        with no control at all, so the pairing is part of the script's contract: order the
        refusal next to the success, and never let a refusal step be dropped on its own.
        """
        script = [step.id for step in walkthrough.STEPS]
        for positive, negative in (
            ("rm-whoami", "rm-spoof-rejected"),
            # Screening: the same lists clear a genuinely clear name, then flag a
            # genuinely designated one, on adjacent steps.
            ("rm-doc1-cdd", "rm-doc1-flagged"),
            # Guardrail: the refusal directly follows the successful dossier builds.
            ("rm-doc1-flagged", "rm-doc1-blocked"),
            # The refusal must come FIRST here: a refused disposition leaves the item
            # pending, so the genuine approval still has something to approve.
            ("ops-hrz7-self-approval", "ops-hrz7-review"),
        ):
            with self.subTest(pair=(positive, negative)):
                self.assertEqual(script.index(negative), script.index(positive) + 1)

    def test_each_paired_step_narrates_why_both_halves_are_shown(self) -> None:
        """The narration has to tie each refusal to the case beside it, out loud.

        Without that sentence the audience hears two unrelated screens rather than one
        control deciding both ways, which is the entire reason the pair exists.
        """
        for step_id in (
            "rm-spoof-rejected",
            "rm-doc1-flagged",
            "rm-doc1-blocked",
            "ops-hrz7-self-approval",
        ):
            step = next(s for s in walkthrough.STEPS if s.id == step_id)
            with self.subTest(step=step_id):
                spoken = step.presenter_notes.lower()
                self.assertTrue(
                    "pair" in spoken or "both halves" in spoken,
                    f"{step_id} never tells the audience the two cases belong together",
                )

    def test_list_mode_needs_no_playwright(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = walkthrough.main(["--journey", "rm", "--list"])
        self.assertEqual(code, 0)
        self.assertIn("rm-doc1-cdd: Assess a real clean subject", output.getvalue())
        # The rehearsal has to include the opening, or a presenter practises 15 steps
        # without the frame that makes them mean anything.
        self.assertIn("OPENING NOTES:", output.getvalue())
        self.assertIn("three separate places", output.getvalue())

    def test_narration_mode_prints_only_spoken_words(self) -> None:
        """The voice-over feed carries no step ids, numbers or titles to edit out."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = walkthrough.main(["--journey", "rm", "--narration"])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("three separate places", text)  # the opening is included
        self.assertNotIn("rm-doc1-cdd", text)
        self.assertNotIn("OPENING NOTES:", text)
        self.assertNotIn("01.", text)

    def test_presenter_notes_are_narration_without_demo_mechanics(self) -> None:
        """The notes are spoken aloud (or voiced by a speech synthesiser), so they are prose.

        Two things break that: demo mechanics the audience should never hear about, and
        anything a narrator cannot read cleanly, which in practice means code identifiers,
        addresses and the script's own step ids.
        """
        forbidden = (
            "fictional",
            "synthetic",
            "localhost",
            "127.0.0.1",
            "preloaded",
            "seed",
            "mock",
            "stub",
            "selector",
            "iframe",
            "workaround",
            "http",
            "`",
            "_",
        )
        # Only the hyphenated ids: those are code names a narrator would have to spell
        # out. A bare word like "close" is also a step id and also ordinary English.
        step_ids = {step.id for step in walkthrough.STEPS if "-" in step.id}
        spoken = [(step.id, step.presenter_notes) for step in walkthrough.STEPS]
        spoken += [
            (f"{step.id} (hosted)", step.hosted_notes)
            for step in walkthrough.STEPS
            if step.hosted_notes is not None
        ]
        spoken.append(("hosted opening", walkthrough.HOSTED_OPENING_NOTES))
        for step_id, notes in spoken:
            with self.subTest(step=step_id):
                self.assertFalse(any(word in notes.lower() for word in forbidden))
                self.assertFalse(any(other in notes for other in step_ids))
                # Prose, not a form: several sentences, and no label-and-colon headings.
                sentences = [part for part in notes.split(". ") if part.strip()]
                self.assertGreaterEqual(len(sentences), 2)
                for line in notes.splitlines():
                    self.assertFalse(
                        _LABEL_HEADING.match(line), f"heading-style label in {step_id}: {line!r}"
                    )

    def test_the_opening_frames_all_three_portability_layers(self) -> None:
        """The audience is given the frame before the first screen, not after the last.

        The demonstration answers one question at three separate layers; if the opening
        names fewer than three, whole steps land as product features rather than as
        evidence for a specific claim.
        """
        opening = walkthrough.OPENING_NOTES.lower()
        for layer in ("experience and identity layer", "processing layer", "data layer"):
            self.assertIn(layer, opening)
        # The pairing convention is also set up before it is first used.
        self.assertIn("only ever says yes", opening)

    def test_portability_is_carried_through_the_script_not_only_the_opening(self) -> None:
        """Each layer is picked up again on the step where it is actually visible."""
        notes = {step.id: step.presenter_notes.lower() for step in walkthrough.STEPS}
        self.assertIn("experience layer", notes["rm-open"])
        # Two host frameworks, one contract: the experience-layer claim made concrete.
        self.assertIn("share no user interface code", notes["ops-open"])
        # The model is replaceable only because the consequential part is not the model.
        self.assertIn("replace and a model you cannot", notes["ops-doc4-ucp600"])
        # Knowledge in exportable records rather than welded into weights.
        self.assertIn("weights", notes["rm-doc3-briefing"])
        self.assertIn("weights", notes["ops-rsk1-compliance"])
        # The index is derived; the source record is what is authoritative.
        self.assertIn("rebuilt on different search technology", notes["ops-doc2-credit-memo"])
        # The sign-off is a record held to the same standard as the rest of the evidence.
        self.assertIn("tamper-evident", notes["ops-hrz7-review"])
        # The close returns to the four questions the whole demonstration answers.
        self.assertIn("four questions", notes["close"])


class HostedWalkthroughTests(unittest.TestCase):
    """The gcp target: the deployed portal's honest subset, with hosted narration."""

    def tearDown(self) -> None:
        walkthrough._HOSTED = False
        walkthrough._CONFIRM_INPUTS = False

    def test_hosted_rm_script_is_the_deployed_subset(self) -> None:
        self.assertEqual(
            [step.id for step in walkthrough.selected_steps("rm", hosted=True)],
            ["rm-open", "rm-spoof-rejected", "rm-doc1-cdd", "close"],
        )

    def test_hosted_selection_excludes_apps_the_deployment_does_not_embed(self) -> None:
        # The Ops journey's apps are not deployed, so a hosted ops run is only the close.
        self.assertEqual(
            [step.id for step in walkthrough.selected_steps("ops", hosted=True)],
            ["close"],
        )
        with self.assertRaisesRegex(ValueError, "unknown or excluded step"):
            walkthrough.selected_steps("rm", "rm-doc1-flagged", hosted=True)

    def test_hosted_notes_exist_only_on_hosted_steps(self) -> None:
        for step in walkthrough.STEPS:
            with self.subTest(step=step.id):
                if step.hosted_notes is not None:
                    self.assertTrue(step.hosted, f"{step.id} narrates a run it never joins")

    def test_hosted_opening_frames_the_flip_and_the_honest_boundaries(self) -> None:
        opening = walkthrough.HOSTED_OPENING_NOTES.lower()
        # The flip: same code, different profile, managed services.
        self.assertIn("profile string", opening)
        self.assertIn("managed", opening)
        # The honesty said before any screen: reference status and the screening stand-in.
        self.assertIn("reference deployment", opening)
        self.assertIn("stand-in", opening)
        # The pair claim: agreement is asserted by a comparison, not by the eye.
        self.assertIn("refuses to pass", opening)

    def test_hosted_list_needs_no_deployment_inputs(self) -> None:
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                code = walkthrough.main(["--journey", "rm", "--target", "gcp", "--list"])
        finally:
            walkthrough._HOSTED = False
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("rm-doc1-cdd", text)
        self.assertNotIn("rm-doc1-flagged", text)
        self.assertIn("reference deployment", text)

    def test_hosted_preflight_expects_the_managed_profile(self) -> None:
        from unittest import mock

        walkthrough._HOSTED = True
        with mock.patch.object(walkthrough, "_doc1_manifest", lambda: {}):
            page = _FakePage(profile="gcp")
            walkthrough._preflight(page, walkthrough.selected_steps("rm", hosted=True))
            local_page = _FakePage(profile="live")
            with self.assertRaisesRegex(RuntimeError, r"profile 'live'"):
                walkthrough._preflight(local_page, walkthrough.selected_steps("rm", hosted=True))


class ConfirmInputsTests(unittest.TestCase):
    """The fill-then-hold contract: inputs are confirmed before every submission."""

    def tearDown(self) -> None:
        walkthrough._CONFIRM_INPUTS = False

    def test_every_fill_and_submit_step_confirms_its_inputs(self) -> None:
        """Each step that fills a form holds via the shared gate before submitting.

        Source-level on purpose: the gate must sit between the fill and the click, and a
        step that grows a new submission without the hold should fail here rather than
        surprise a presenter mid-demo.
        """
        import inspect

        submitting = {
            "rm-doc1-cdd",
            "rm-doc1-flagged",
            "rm-doc1-blocked",
            "rm-doc3-briefing",
            "ops-doc2-credit-memo",
            "ops-doc4-ucp600",
            "ops-rsk1-compliance",
            "ops-hrz7-review",
        }
        for step in walkthrough.STEPS:
            if step.id not in submitting:
                continue
            with self.subTest(step=step.id):
                source = inspect.getsource(step.action)
                self.assertIn("_inputs_ready(", source)
                # The hold precedes the step's final submitting click.
                self.assertLess(source.index("_inputs_ready("), source.rindex(".click()"))

    def test_the_hold_only_engages_when_asked(self) -> None:
        from unittest import mock

        prompts: list[str] = []
        with mock.patch("builtins.input", side_effect=lambda p="": prompts.append(p)):
            walkthrough._CONFIRM_INPUTS = False
            walkthrough._inputs_ready("something")
            self.assertEqual(prompts, [])
            walkthrough._CONFIRM_INPUTS = True
            walkthrough._inputs_ready("something")
        self.assertEqual(len(prompts), 1)
        self.assertIn("submit", prompts[0].lower())


if __name__ == "__main__":
    unittest.main()
