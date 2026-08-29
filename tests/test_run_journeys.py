"""Unit coverage for the local demo launcher, without starting child processes."""

from __future__ import annotations

import importlib.util
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

from journey_portal.config import Settings, load_journeys_mapping
from journey_portal.domain.catalog import JourneyCatalog


@pytest.fixture(scope="module")
def launcher_module() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "run_journeys.py"
    spec = importlib.util.spec_from_file_location("run_journeys_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_backend_interpreter_prefers_the_app_virtualenv(
    launcher_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    interpreter.chmod(0o755)

    assert launcher_module._backend_interpreter(tmp_path) == str(interpreter)
    assert capsys.readouterr().out == ""


def test_backend_interpreter_warns_before_falling_back(
    launcher_module: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert launcher_module._backend_interpreter(tmp_path) == sys.executable
    assert "no executable .venv/bin/python" in capsys.readouterr().out


def test_journey_subset_uses_only_its_configured_apps(launcher_module: ModuleType) -> None:
    catalog = JourneyCatalog.from_mapping(load_journeys_mapping(Settings.load().journeys_path))

    assert launcher_module._selected_app_ids(catalog, ("rm",)) == ("cdd-sow-research", "loan-document-intelligence", "cio-advisory")
    assert launcher_module._selected_app_ids(catalog, ("ops",)) == ("credit-memo-drafting", "trade-finance-checker", "compliance-advisory", "human-review-console")


def test_hrz7_ui_uses_the_portal_relative_review_api(launcher_module: ModuleType) -> None:
    environment = launcher_module._ui_environment("human-review-console")

    assert environment["NEXT_PUBLIC_REVIEW_API_URL"] == "/apps/human-review-console/api"
    assert environment["NEXT_PUBLIC_API_BASE"] == "/apps/human-review-console/api"
    assert all("localhost" not in value for value in environment.values())


def test_other_uis_do_not_receive_the_hrz7_specific_variable(
    launcher_module: ModuleType,
) -> None:
    assert "NEXT_PUBLIC_REVIEW_API_URL" not in launcher_module._ui_environment("cdd-sow-research")


def test_doc1_ui_uses_the_canonical_agent_artifact_path(
    launcher_module: ModuleType,
) -> None:
    environment = launcher_module._ui_environment("cdd-sow-research")

    assert environment["NEXT_PUBLIC_BASE_PATH"] == "/agent"
    assert environment["NEXT_PUBLIC_API_BASE"] == "/agent/api"


def test_launcher_wires_doc1_to_hrz7_as_a_loopback_service_producer(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    repos = (
        ("cdd-sow-research", "cdd_sow_research"),
        ("human-review-console", "review_console"),
    )
    for repo_name, package in repos:
        (workspace / repo_name / "src" / package / "api").mkdir(parents=True)
        (workspace / repo_name / "src" / package / "api" / "app.py").touch()
    monkeypatch.setattr(launcher_module, "_WORKSPACE", workspace)
    monkeypatch.setattr(
        launcher_module,
        "_APP_REPOS",
        {"cdd-sow-research": "cdd-sow-research", "human-review-console": "human-review-console"},
    )
    monkeypatch.setenv("JOURNEY_DEMO_S2S_TOKEN", "synthetic-test-token")
    launcher = launcher_module.Launcher(with_shells=False)
    launcher._spawn = Mock()

    launcher.launch_app("cdd-sow-research", api_port=8090, ui_port=3001)
    launcher.launch_app("human-review-console", api_port=8087, ui_port=3007)

    doc1_backend = launcher._spawn.call_args_list[0]
    hrz7_backend = launcher._spawn.call_args_list[1]
    assert doc1_backend.kwargs["env"] == {
        "PYTHONPATH": "src",
        # Named, never inherited. Every app the launcher starts is told its posture.
        "CDD_PROFILE": launcher_module._PORTAL_LOCAL_PROFILE,
        "CDD_CHANNEL_PROFILE": "native",
        "CDD_IDENTITY_PROFILE": "local-persona",
        "CDD_ALLOW_INSECURE_DEMO": "1",
        "CDD_HRZ7_URL": "http://127.0.0.1:8087",
        "CDD_LOCAL_REVIEW_OUTBOX": str(launcher_module._CDD_REVIEW_OUTBOX),
        "CDD_S2S_TOKEN": "synthetic-test-token",
    }
    assert "CDD_LOCAL_REVIEW_URL" not in doc1_backend.kwargs["env"]
    assert hrz7_backend.kwargs["env"] == {
        "PYTHONPATH": "src",
        # Named, never inherited: Hrz7 refuses a seeded persona whose profile it was not
        # given, and it refuses it on the write path while healthz still reads green, so a
        # launcher that omits this produces a queue that lists and disposes of nothing.
        "REVIEW_PROFILE": launcher_module._PORTAL_LOCAL_PROFILE,
        "REVIEW_DB_PATH": str(launcher_module._REVIEW_CONSOLE_DB),
        "REVIEW_S2S_TOKEN": "synthetic-test-token",
    }


def test_live_flag_adds_only_doc1_live_overrides(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "cdd-sow-research" / "src" / "cdd_sow_research" / "api").mkdir(parents=True)
    (workspace / "cdd-sow-research" / "src" / "cdd_sow_research" / "api" / "app.py").touch()
    monkeypatch.setattr(launcher_module, "_WORKSPACE", workspace)
    monkeypatch.setattr(launcher_module, "_APP_REPOS", {"cdd-sow-research": "cdd-sow-research"})
    monkeypatch.setenv("JOURNEY_DEMO_S2S_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fictional-demo-project")
    monkeypatch.delenv("CDD_TRIAGE_MODEL", raising=False)
    monkeypatch.delenv("CDD_MAX_BODY_BYTES", raising=False)
    launcher = launcher_module.Launcher(with_shells=False, live=True)
    launcher._spawn = Mock()

    launcher.launch_app("cdd-sow-research", api_port=8090, ui_port=3001)

    assert launcher._spawn.call_args_list[0].kwargs["env"] == {
        "PYTHONPATH": "src",
        "CDD_CHANNEL_PROFILE": "native",
        "CDD_IDENTITY_PROFILE": "local-persona",
        "CDD_ALLOW_INSECURE_DEMO": "1",
        "CDD_HRZ7_URL": "http://127.0.0.1:8087",
        "CDD_LOCAL_REVIEW_OUTBOX": str(launcher_module._CDD_REVIEW_OUTBOX),
        "CDD_S2S_TOKEN": "synthetic-test-token",
        "CDD_PROFILE": "live",
        "CDD_TRIAGE_MODEL": "gemini-3.5-flash",
        "CDD_MAX_BODY_BYTES": "33554432",
        "GOOGLE_CLOUD_PROJECT": "fictional-demo-project",
    }


def test_doc1_hosted_launcher_selects_iap_without_demo_acknowledgement(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOURNEY_DOC1_IDENTITY_PROFILE", "iap")
    monkeypatch.setenv("PORTAL_PROFILE", "gcp")

    assert launcher_module.Launcher._doc1_security_environment() == {
        "CDD_CHANNEL_PROFILE": "native",
        "CDD_IDENTITY_PROFILE": "iap",
    }


def test_doc1_launcher_rejects_an_unreviewed_identity(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOURNEY_DOC1_IDENTITY_PROFILE", "oidc-session")

    with pytest.raises(ValueError, match="JOURNEY_DOC1_IDENTITY_PROFILE"):
        launcher_module.Launcher._doc1_security_environment()


@pytest.mark.parametrize(
    ("identity", "portal_profile"),
    [
        ("local-persona", "gcp"),
        ("iap", "local"),
        ("iap", "platform"),
    ],
)
def test_doc1_launcher_rejects_contradictory_portal_identity_profiles(
    launcher_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    portal_profile: str,
) -> None:
    monkeypatch.setenv("JOURNEY_DOC1_IDENTITY_PROFILE", identity)
    monkeypatch.setenv("PORTAL_PROFILE", portal_profile)

    with pytest.raises(ValueError, match="requires PORTAL_PROFILE"):
        launcher_module.Launcher._doc1_security_environment()


@pytest.mark.parametrize(
    ("identity", "portal_profile", "allow_demo"),
    [
        ("local-persona", "local", True),
        ("iap", "gcp", False),
    ],
)
def test_doc1_and_portal_child_environments_are_paired(
    launcher_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    portal_profile: str,
    allow_demo: bool,
) -> None:
    monkeypatch.setenv("JOURNEY_DOC1_IDENTITY_PROFILE", identity)
    monkeypatch.setenv("PORTAL_PROFILE", portal_profile)
    launcher = launcher_module.Launcher(with_shells=False)
    launcher._spawn = Mock()

    doc1_environment = launcher._doc1_security_environment()
    launcher.launch_portal()

    assert doc1_environment["CDD_IDENTITY_PROFILE"] == identity
    assert ("CDD_ALLOW_INSECURE_DEMO" in doc1_environment) is allow_demo
    assert launcher._spawn.call_args.kwargs["env"]["PORTAL_PROFILE"] == portal_profile


def test_secure_doc1_child_does_not_inherit_local_demo_acknowledgement(
    launcher_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class _Process:
        pid = 123

        def poll(self) -> None:
            return None

    def fake_popen(*args: object, **kwargs: object) -> _Process:
        captured.update(kwargs)
        return _Process()

    monkeypatch.setenv("JOURNEY_DOC1_IDENTITY_PROFILE", "iap")
    monkeypatch.setenv("PORTAL_PROFILE", "gcp")
    monkeypatch.setenv("CDD_ALLOW_INSECURE_DEMO", "1")
    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)
    launcher = launcher_module.Launcher(with_shells=False)

    launcher._spawn(
        "doc1-backend",
        ["python", "-m", "uvicorn"],
        cwd=tmp_path,
        env=launcher._doc1_security_environment(),
        readiness_url="http://127.0.0.1:8090/healthz",
    )

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["CDD_IDENTITY_PROFILE"] == "iap"
    assert "CDD_ALLOW_INSECURE_DEMO" not in environment


def test_live_overrides_yield_to_an_operator_export(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CDD_TRIAGE_MODEL", "gemini-3.5-flash-operator-pin")
    monkeypatch.setenv("CDD_MAX_BODY_BYTES", "1048576")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    environment = launcher_module.Launcher._live_doc1_environment()

    assert environment["CDD_TRIAGE_MODEL"] == "gemini-3.5-flash-operator-pin"
    assert environment["CDD_MAX_BODY_BYTES"] == "1048576"
    # Never invented: an unset project is reported by the launch plan instead.
    assert "GOOGLE_CLOUD_PROJECT" not in environment


def test_live_flag_raises_only_the_portal_upstream_timeout(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PORTAL_UPSTREAM_TIMEOUT", raising=False)
    launcher = launcher_module.Launcher(with_shells=False, live=True)
    launcher._spawn = Mock()

    launcher.launch_portal()

    assert launcher._spawn.call_args.kwargs["env"] == {
        "PYTHONPATH": "src",
        "PORTAL_FRAME_ANCESTORS": launcher_module._SHELL_ORIGINS,
        "PORTAL_PROFILE": "local",
        "PORTAL_UPSTREAM_TIMEOUT": "600",
    }


def test_portal_keeps_the_default_timeout_without_the_live_flag(
    launcher_module: ModuleType,
) -> None:
    launcher = launcher_module.Launcher(with_shells=False)
    launcher._spawn = Mock()

    launcher.launch_portal()

    assert "PORTAL_UPSTREAM_TIMEOUT" not in launcher._spawn.call_args.kwargs["env"]


def test_dry_run_with_live_reports_the_plan_without_starting_anything(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prepare_state = Mock()
    popen = Mock()
    monkeypatch.setattr(launcher_module, "_prepare_presenter_state", prepare_state)
    monkeypatch.setattr(launcher_module.subprocess, "Popen", popen)
    # The live plan probes for an already-running model server (read-only lsof); pin it to
    # "nothing listening" so the plan is deterministic regardless of the host's :8001 state.
    monkeypatch.setattr(launcher_module.Launcher, "_listener_pids", staticmethod(lambda port: ()))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("JOURNEY_MODEL_SERVER_CMD", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_journeys.py", "--dry-run", "--live"])

    assert launcher_module.main() == 0

    output = capsys.readouterr().out
    assert "doc1 profile      live" in output
    assert "PORTAL_UPSTREAM_TIMEOUT=600s" in output
    assert "warning GOOGLE_CLOUD_PROJECT is not set" in output
    # With nothing on the port and no launch command, the plan warns rather than promising
    # a model server it cannot bring up.
    assert "JOURNEY_MODEL_SERVER_CMD is unset" in output
    prepare_state.assert_not_called()
    popen.assert_not_called()


def test_fresh_state_removes_only_launcher_owned_review_databases(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "presenter-state"
    state_dir.mkdir()
    doc1 = state_dir / "doc1.sqlite3"
    hrz7 = state_dir / "hrz7.sqlite3"
    unrelated = state_dir / "keep.sqlite3"
    for database in (doc1, hrz7):
        database.write_text("database", encoding="utf-8")
        Path(f"{database}-wal").write_text("wal", encoding="utf-8")
        Path(f"{database}-shm").write_text("shm", encoding="utf-8")
    unrelated.write_text("unrelated", encoding="utf-8")
    monkeypatch.setattr(launcher_module, "_PRESENTER_STATE_DIR", state_dir)
    monkeypatch.setattr(launcher_module, "_CDD_REVIEW_OUTBOX", doc1)
    monkeypatch.setattr(launcher_module, "_REVIEW_CONSOLE_DB", hrz7)

    removed = launcher_module._reset_presenter_state()

    assert set(removed) == {
        doc1,
        Path(f"{doc1}-wal"),
        Path(f"{doc1}-shm"),
        hrz7,
        Path(f"{hrz7}-wal"),
        Path(f"{hrz7}-shm"),
    }
    assert unrelated.read_text(encoding="utf-8") == "unrelated"


def test_prepare_presenter_state_creates_the_owned_directory(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "nested" / "presenter-state"
    monkeypatch.setattr(launcher_module, "_PRESENTER_STATE_DIR", state_dir)

    launcher_module._prepare_presenter_state()

    assert state_dir.is_dir()


def test_fresh_state_refuses_a_database_outside_its_owned_directory(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "presenter-state"
    state_dir.mkdir()
    monkeypatch.setattr(launcher_module, "_PRESENTER_STATE_DIR", state_dir)
    monkeypatch.setattr(launcher_module, "_CDD_REVIEW_OUTBOX", tmp_path / "outside.sqlite3")
    monkeypatch.setattr(launcher_module, "_REVIEW_CONSOLE_DB", state_dir / "hrz7.sqlite3")

    with pytest.raises(RuntimeError, match="escaped"):
        launcher_module._reset_presenter_state()


def test_dry_run_with_fresh_state_does_not_remove_files(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset = Mock()
    monkeypatch.setattr(launcher_module, "_reset_presenter_state", reset)
    monkeypatch.setattr(sys, "argv", ["run_journeys.py", "--dry-run", "--fresh-state"])

    assert launcher_module.main() == 0
    reset.assert_not_called()


def test_launcher_does_not_leak_service_token_to_portal_or_shells(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class _Process:
        pid = 123

        def poll(self) -> None:
            return None

    def fake_popen(*args: object, **kwargs: object) -> _Process:
        captured.update(kwargs)
        return _Process()

    monkeypatch.setenv("JOURNEY_DEMO_S2S_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("CDD_S2S_TOKEN", "must-not-leak")
    monkeypatch.setenv("REVIEW_S2S_TOKEN", "must-not-leak")
    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)
    launcher = launcher_module.Launcher(with_shells=False)

    launcher.launch_portal()

    env = captured["env"]
    assert isinstance(env, dict)
    assert "JOURNEY_DEMO_S2S_TOKEN" not in env
    assert "CDD_S2S_TOKEN" not in env
    assert "REVIEW_S2S_TOKEN" not in env


class _LiveProcess:
    def poll(self) -> None:
        return None


class _ExitedProcess:
    def poll(self) -> int:
        return 17


def test_readiness_reports_ready_processes(
    launcher_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = launcher_module.Launcher(with_shells=False)
    launcher._readiness.append(
        launcher_module._ReadinessCheck(
            "doc1-backend", "http://127.0.0.1:8090/healthz", _LiveProcess()
        )
    )
    launcher._probe = lambda url: "HTTP 200"

    assert launcher.wait_for_readiness(timeout=0.01, poll_interval=0) is True
    assert "doc1-backend           READY   HTTP 200" in capsys.readouterr().out


def test_readiness_reports_the_failed_app_by_name(
    launcher_module: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = launcher_module.Launcher(with_shells=False)
    launcher._readiness.append(
        launcher_module._ReadinessCheck(
            "doc4-backend", "http://127.0.0.1:8094/healthz", _ExitedProcess()
        )
    )

    assert launcher.wait_for_readiness(timeout=0.01, poll_interval=0) is False
    output = capsys.readouterr().out
    assert "doc4-backend           FAILED  exited with code 17 before ready" in output


def test_spawn_creates_a_dedicated_process_session(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _Process:
        pid = 123

        def poll(self) -> None:
            return None

    def fake_popen(*args: object, **kwargs: object) -> _Process:
        captured.update(kwargs)
        return _Process()

    monkeypatch.setattr(launcher_module.subprocess, "Popen", fake_popen)
    launcher = launcher_module.Launcher(with_shells=False)
    launcher._spawn(
        "demo",
        ["demo-command"],
        cwd=tmp_path,
        env={},
        readiness_url="http://127.0.0.1:9000/",
    )

    assert captured["start_new_session"] is (os.name == "posix")


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        return client.connect_ex(("127.0.0.1", port)) == 0


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX teardown contract")
def test_stop_terminates_a_descendant_process_group(launcher_module: ModuleType) -> None:
    child_source = """
import socket
import time

server = socket.socket()
server.bind((\"127.0.0.1\", 0))
server.listen()
print(server.getsockname()[1], flush=True)
time.sleep(60)
"""
    parent_source = f"""
import subprocess
import sys
import time

child_command = [sys.executable, \"-u\", \"-c\", {child_source!r}]
child = subprocess.Popen(child_command, stdout=subprocess.PIPE, text=True)
print(child.stdout.readline(), end=\"\", flush=True)
time.sleep(60)
"""
    parent = subprocess.Popen(
        [sys.executable, "-u", "-c", parent_source],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert parent.stdout is not None
    port = int(parent.stdout.readline())
    assert _port_is_open(port)

    launcher = launcher_module.Launcher(with_shells=False)
    launcher.procs.append(("process-tree", parent))
    try:
        launcher.stop()
    finally:
        if parent.poll() is None:
            os.killpg(parent.pid, signal.SIGKILL)
            parent.wait(timeout=1)

    deadline = time.monotonic() + 2
    while _port_is_open(port) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _port_is_open(port)


def test_termination_handler_stops_the_launcher_before_exiting(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    handlers: dict[int, object] = {}
    stops: list[None] = []
    launcher = launcher_module.Launcher(with_shells=False)

    def capture_handler(signum: int, handler: object) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(launcher_module.signal, "signal", capture_handler)
    launcher.stop = lambda: stops.append(None)

    launcher.install_termination_handler()

    with pytest.raises(SystemExit) as raised:
        handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert raised.value.code == 128 + signal.SIGTERM
    assert stops == [None]


def test_main_stops_after_a_readiness_failure(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    instances: list[object] = []

    class _Launcher:
        def __init__(self, **kwargs: object) -> None:
            self.stops = 0
            instances.append(self)

        def install_termination_handler(self) -> None:
            return None

        def launch_app(self, *args: object) -> None:
            return None

        def launch_portal(self) -> None:
            return None

        def launch_shells(self, *args: object) -> None:
            return None

        def wait_for_readiness(self, **kwargs: object) -> bool:
            return False

        def stop(self) -> None:
            self.stops += 1

        def wait(self, *args: object) -> None:
            raise AssertionError("wait must not run after a readiness failure")

    monkeypatch.setattr(launcher_module, "Launcher", _Launcher)
    monkeypatch.setattr(sys, "argv", ["run_journeys.py", "--journey", "rm", "--no-shells"])

    assert launcher_module.main() == 1
    assert len(instances) == 1
    assert instances[0].stops == 1


def test_ops_shell_binds_the_ipv4_loopback_probe_address(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "ui-ops" / "node_modules").mkdir(parents=True)
    monkeypatch.setattr(launcher_module, "_REPO_ROOT", tmp_path)
    launcher = launcher_module.Launcher(with_shells=True)
    launcher._spawn = Mock()

    launcher.launch_shells(("ops",))

    launcher._spawn.assert_called_once_with(
        "ops-shell",
        ["npm", "start", "--", "--host", "127.0.0.1"],
        cwd=tmp_path / "ui-ops",
        env={"NG_CLI_ANALYTICS": "false"},
        readiness_url="http://127.0.0.1:4200/",
    )


def test_live_model_server_is_reused_when_already_healthy(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A healthy server already on the port is adopted, not restarted, and never spawned.
    monkeypatch.setattr(
        launcher_module.Launcher, "_listener_pids", staticmethod(lambda port: (999,))
    )
    monkeypatch.setattr(launcher_module.Launcher, "_probe", staticmethod(lambda url: "HTTP 200"))
    launcher = launcher_module.Launcher(with_shells=False, live=True)
    launcher._spawn = Mock()

    launcher.launch_model_server()

    launcher._spawn.assert_not_called()
    assert [c.label for c in launcher._readiness] == ["model-server"]
    assert launcher._readiness[0].process is None


def test_live_model_server_is_launched_from_the_env_command_when_absent(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher_module.Launcher, "_listener_pids", staticmethod(lambda port: ()))
    monkeypatch.setenv("JOURNEY_MODEL_SERVER_CMD", "python -m my.model.server --port 8001")
    monkeypatch.delenv("CDD_LIVE_LLM_URL", raising=False)
    launcher = launcher_module.Launcher(with_shells=False, live=True)
    launcher._spawn = Mock()

    launcher.launch_model_server()

    launcher._spawn.assert_called_once()
    call = launcher._spawn.call_args
    assert call.args[1] == ["python", "-m", "my.model.server", "--port", "8001"]
    assert call.kwargs["readiness_url"] == "http://127.0.0.1:8001/health"


def test_live_model_server_absent_and_uncommanded_is_a_named_failure(
    launcher_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher_module.Launcher, "_listener_pids", staticmethod(lambda port: ()))
    monkeypatch.delenv("JOURNEY_MODEL_SERVER_CMD", raising=False)
    launcher = launcher_module.Launcher(with_shells=False, live=True)
    launcher._spawn = Mock()

    launcher.launch_model_server()

    launcher._spawn.assert_not_called()
    assert "model-server" in launcher._startup_failures
