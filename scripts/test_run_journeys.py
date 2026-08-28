#!/usr/bin/env python3
"""Focused launcher checks kept outside the portal's core test gate."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

_SCRIPT = Path(__file__).with_name("run_journeys.py")
sys.path.insert(0, str(_SCRIPT.parent.parent / "src"))
_SPEC = importlib.util.spec_from_file_location("run_journeys", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
run_journeys = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_journeys
_SPEC.loader.exec_module(run_journeys)


class BuiltModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.repo = self.workspace / "demo-app"
        (self.repo / "src" / "demo_app" / "api").mkdir(parents=True)
        (self.repo / "src" / "demo_app" / "api" / "app.py").touch()
        (self.repo / "ui").mkdir()
        (self.repo / "ui" / "package.json").write_text("{}", encoding="utf-8")
        self.workspace_patch = patch.object(run_journeys, "_WORKSPACE", self.workspace)
        self.repos_patch = patch.object(run_journeys, "_APP_REPOS", {"doc1": "demo-app"})
        self.workspace_patch.start()
        self.repos_patch.start()
        self.addCleanup(self.workspace_patch.stop)
        self.addCleanup(self.repos_patch.stop)
        self.addCleanup(self.tempdir.cleanup)

    def test_built_mode_builds_with_embed_env_then_starts(self) -> None:
        launcher = run_journeys.Launcher(with_shells=False, built=True)
        launcher._spawn = Mock()  # type: ignore[method-assign]
        completed = subprocess.CompletedProcess(["npm", "run", "build"], 0)

        with (
            patch.object(launcher, "_clear_stale_listener", return_value=True),
            patch.object(run_journeys.subprocess, "run", return_value=completed) as build,
        ):
            launcher.launch_app("doc1", api_port=9001, ui_port=9002)

        expected_env = {
            "NEXT_PUBLIC_BASE_PATH": "/agent",
            "NEXT_PUBLIC_API_BASE": "/agent/api",
            "NEXT_PUBLIC_EMBED": "1",
            "NEXT_PUBLIC_FRAME_ANCESTORS": "'self'",
        }
        build.assert_called_once_with(
            ["npm", "run", "build"],
            cwd=str(self.repo / "ui"),
            env={**run_journeys.os.environ, **expected_env},
            check=False,
        )
        ui_call = launcher._spawn.call_args_list[1]  # type: ignore[union-attr]
        self.assertEqual(ui_call.args, ("doc1-ui", ["npm", "run", "start", "--", "--port", "9002"]))
        self.assertEqual(ui_call.kwargs["env"], expected_env)
        self.assertEqual(ui_call.kwargs["readiness_url"], "http://127.0.0.1:9002/agent")

    def test_dev_mode_does_not_build_and_uses_next_dev(self) -> None:
        launcher = run_journeys.Launcher(with_shells=False)
        launcher._spawn = Mock()  # type: ignore[method-assign]

        with patch.object(run_journeys.subprocess, "run") as build:
            launcher.launch_app("doc1", api_port=9001, ui_port=9002)

        build.assert_not_called()
        ui_call = launcher._spawn.call_args_list[1]  # type: ignore[union-attr]
        self.assertEqual(ui_call.args, ("doc1-ui", ["npm", "run", "dev", "--", "--port", "9002"]))
        self.assertEqual(ui_call.kwargs["readiness_url"], "http://127.0.0.1:9002/agent")

    def test_failed_built_ui_is_not_started(self) -> None:
        launcher = run_journeys.Launcher(with_shells=False, built=True)
        launcher._spawn = Mock()  # type: ignore[method-assign]
        failed = subprocess.CompletedProcess(["npm", "run", "build"], 1)

        with (
            patch.object(launcher, "_clear_stale_listener", return_value=True),
            patch.object(run_journeys.subprocess, "run", return_value=failed),
        ):
            launcher.launch_app("doc1", api_port=9001, ui_port=9002)

        self.assertEqual(launcher._spawn.call_count, 1)  # type: ignore[union-attr]
        self.assertEqual(launcher._startup_failures["doc1-ui"], "production build failed (exit 1)")

    def test_built_mode_stops_a_stale_listener_from_the_expected_repo(self) -> None:
        launcher = run_journeys.Launcher(with_shells=False, built=True)
        expected_cwd = self.repo / "ui"

        with (
            patch.object(launcher, "_listener_pids", side_effect=((1234,), ())) as listeners,
            patch.object(launcher, "_process_cwd", return_value=expected_cwd.resolve()),
            patch.object(run_journeys.os, "kill") as kill,
        ):
            cleared = launcher._clear_stale_listener("doc1-ui", 9002, expected_cwd)

        self.assertTrue(cleared)
        kill.assert_called_once_with(1234, run_journeys.signal.SIGTERM)
        self.assertEqual(listeners.call_args_list[0].args, (9002,))

    def test_built_mode_refuses_to_stop_a_foreign_listener(self) -> None:
        launcher = run_journeys.Launcher(with_shells=False, built=True)

        with (
            patch.object(launcher, "_listener_pids", return_value=(1234,)),
            patch.object(launcher, "_process_cwd", return_value=Path("/tmp/other-app")),
            patch.object(run_journeys.os, "kill") as kill,
        ):
            cleared = launcher._clear_stale_listener("doc1-ui", 9002, self.repo / "ui")

        self.assertFalse(cleared)
        kill.assert_not_called()
        self.assertIn("doc1-ui", launcher._startup_failures)


class BackendProfileTests(unittest.TestCase):
    """Every backend the launcher starts is told which posture it is running.

    These apps refuse a seeded persona whose profile was INHERITED rather than chosen, and
    they refuse it on the WRITE path while their health check still reads green. A launcher
    that omits the profile therefore produces a demo where the queue lists and disposes of
    nothing, and nothing on screen says why. That failure is invisible to a readiness table,
    so it has to be caught here.
    """

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tempdir.name)
        self.repo = self.workspace / "review-app"
        (self.repo / "src" / "review_console" / "api").mkdir(parents=True)
        (self.repo / "src" / "review_console" / "api" / "app.py").touch()
        (self.repo / "ui").mkdir()
        (self.repo / "ui" / "package.json").write_text("{}", encoding="utf-8")
        for patcher in (
            patch.object(run_journeys, "_WORKSPACE", self.workspace),
            patch.object(run_journeys, "_APP_REPOS", {"hrz7": "review-app"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tempdir.cleanup)

    def test_hrz7_backend_is_told_its_profile(self) -> None:
        launcher = run_journeys.Launcher(with_shells=False)
        launcher._spawn = Mock()  # type: ignore[method-assign]

        launcher.launch_app("hrz7", api_port=9001, ui_port=9002)

        backend_call = launcher._spawn.call_args_list[0]  # type: ignore[union-attr]
        self.assertEqual(backend_call.args[0], "hrz7-backend")
        backend_env = backend_call.kwargs["env"]
        self.assertEqual(backend_env["REVIEW_PROFILE"], run_journeys._PORTAL_LOCAL_PROFILE)
        # The database and the service credential travel with it; a profile without them
        # would start, and they without the profile is the failure this test exists for.
        self.assertIn("REVIEW_DB_PATH", backend_env)
        self.assertIn("REVIEW_S2S_TOKEN", backend_env)


if __name__ == "__main__":
    unittest.main()
