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
        for step in walkthrough.STEPS:
            with self.subTest(step=step.id):
                notes = step.presenter_notes
                self.assertFalse(any(word in notes.lower() for word in forbidden))
                self.assertFalse(any(other in notes for other in step_ids))
                # Prose, not a form: several sentences, and no label-and-colon headings.
                sentences = [part for part in notes.split(". ") if part.strip()]
                self.assertGreaterEqual(len(sentences), 2)
                for line in notes.splitlines():
                    self.assertFalse(
                        _LABEL_HEADING.match(line), f"heading-style label in {step.id}: {line!r}"
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


if __name__ == "__main__":
    unittest.main()
