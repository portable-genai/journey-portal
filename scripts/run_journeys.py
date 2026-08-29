#!/usr/bin/env python3
"""Launch the whole journey-portal demo with one command (dev orchestration, outside the gate).

Brings up, as child processes:

* every embedded app referenced by ``config/journeys.yaml`` - each app's FastAPI backend (on its
  configured api port) and its Next.js UI (on its configured ui port, built with its canonical
  base path plus embed env so it mounts same-origin under the portal);
* the portal BFF (``uvicorn journey_portal.api.app:app`` on :8110);
* one shell per journey - the React shell in `ui-rm` serves every journey but `ops`, each
  on its own port, and `ops` keeps its Angular shell so two front-end stacks are proved.

Each app repo is discovered next to this one in the polyrepo workspace; its backend package is
found by globbing ``src/*/api/app.py`` so no per-repo package name is hard-coded. A missing repo
(or one whose deps are not installed) is recorded in the readiness table before the launcher stops
the partial stack. ``--dry-run`` prints the launch plan and exits, which is safe with no repos
installed.

``--built`` makes embedded UIs production-shaped: it builds each UI once, then serves it with
``next start``. The default remains ``next dev`` for fast local iteration.

``--fresh-state`` removes only the launcher-owned synthetic Doc1 delivery outbox and Hrz7 review
queue before processes start.  Their paths live under ``scripts/out/presenter-state`` so this
never resets either sibling repo's general local data.

``--live`` runs the Doc1 backend in its ``live`` profile: real uploaded documents, a local
OpenAI-compatible model server for generation and page transcription, and Gemini ``google_search``
grounding for adverse media and the corporate registry (which needs Google ADC plus
``GOOGLE_CLOUD_PROJECT``).  A live dossier build takes minutes, so the portal BFF also gets a
raised ``PORTAL_UPSTREAM_TIMEOUT``.  Nothing else about the launch changes, and every other
embedded app stays on its offline profile.

This is a convenience launcher, not production wiring: in production the BFF and apps are separate
Cloud Run services behind one HTTPS load balancer + IAP (see docs/embedding-and-identity.md).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from hex_service_kit.netdefaults import read_env_setting

from journey_portal.config import Settings, load_journeys_mapping
from journey_portal.domain.catalog import JourneyCatalog

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE = _REPO_ROOT.parent
#: Journey key -> the port its shell serves on. The React shell in `ui-rm` renders whichever
#: journey it is told to, so every journey but `ops` (which keeps its own Angular shell, to
#: prove two front-end stacks against one portal contract) is served by that one codebase.
_SHELL_PORTS: dict[str, int] = {"rm": 3000, "mkt": 3001, "gov": 3002, "svc": 3003}
_OPS_SHELL_PORT = 4200
_SHELL_ORIGINS = " ".join(
    f"http://localhost:{port}" for port in (*_SHELL_PORTS.values(), _OPS_SHELL_PORT)
)
# This credential exists solely inside launcher-created backend processes.  It is intentionally
# synthetic and never passed to a shell, iframe, or portal BFF environment.  A user may override
# it for a local integration test, but production wiring must inject a secret through its runtime.
_LOCAL_DEMO_S2S_TOKEN_ENV = "JOURNEY_DEMO_S2S_TOKEN"
_DEFAULT_LOCAL_DEMO_S2S_TOKEN = "journey-demo-synthetic-s2s-token"
_DOC1_IDENTITY_ENV = "JOURNEY_DOC1_IDENTITY_PROFILE"
_DOC1_LOCAL_IDENTITY = "local-persona"
_DOC1_HOSTED_IDENTITY = "iap"
_PORTAL_PROFILE_ENV = "PORTAL_PROFILE"
_PORTAL_LOCAL_PROFILE = "local"
_PORTAL_HOSTED_PROFILE = "gcp"
_S2S_SECRET_ENVIRONMENTS = frozenset(
    {_LOCAL_DEMO_S2S_TOKEN_ENV, "CDD_S2S_TOKEN", "REVIEW_S2S_TOKEN"}
)
_INSECURE_DOC1_ACK_ENV = "CDD_ALLOW_INSECURE_DEMO"
_GOOGLE_PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
_PORTAL_UPSTREAM_TIMEOUT_ENV = "PORTAL_UPSTREAM_TIMEOUT"
# ``--live`` overrides for the Doc1 backend, applied on top of its ordinary demo environment.
# An operator who already exported one of these keeps their own value; only the profile itself is
# forced, because selecting it is what the flag means (Doc1's own docs tell operators to export
# CDD_PROFILE=local, and a stale export must not silently turn --live into a no-op).
_LIVE_DOC1_PROFILE = "live"
_LIVE_DOC1_DEFAULTS: dict[str, str] = {
    # The default triage model (gemini-3.1-flash-lite) is not served in the pinned
    # asia-southeast1 region, so grounded adverse-media and registry research 404s without this.
    "CDD_TRIAGE_MODEL": "gemini-3.5-flash",
    # 32 MiB: real PDF uploads must not be rejected by the request-body cap.
    "CDD_MAX_BODY_BYTES": "33554432",
}
# A live dossier makes several local model calls, so the proxy must outlive the default 30s.
_LIVE_PORTAL_UPSTREAM_TIMEOUT = "600"
# The REAL synced watchlist snapshot Doc1's sibling repo writes (scripts/sync_sanctions.py).
# When present it becomes Doc1's screening list under --live; without it screening would
# silently run against the bundled FICTIONAL fixture, which a live demo must not do.
_LIVE_SANCTIONS_ENV = "CDD_LOCAL_SANCTIONS"
_LIVE_SANCTIONS_SNAPSHOT = "scripts/out/sanctions/current.json"
# The local OpenAI-compatible model server Doc1's live profile calls for generation and
# page transcription. Doc1 names the endpoint via CDD_LIVE_LLM_URL; the launcher reads the
# same variable to learn which port it must answer on and derives the health URL from it.
# Its launch command is machine-specific (the server lives outside this workspace), so it
# is supplied by the operator via JOURNEY_MODEL_SERVER_CMD rather than hardcoded; an
# already-running server on the port is reused untouched.
_MODEL_SERVER_URL_ENV = "CDD_LIVE_LLM_URL"
_MODEL_SERVER_CMD_ENV = "JOURNEY_MODEL_SERVER_CMD"
_DEFAULT_MODEL_SERVER_URL = "http://127.0.0.1:8001/chat/completions"

# ``--live`` now covers every journey app, not only Doc1: each of these runs its own live
# profile (real data sources + the shared local model server; no fictional seeds). The
# tuple is (profile env var, the app's own model-server URL env var): the launcher forces
# the profile (selecting it is what the flag means) and mirrors the one model-server
# endpoint into each app's URL variable so a non-default port set once via
# CDD_LIVE_LLM_URL reaches every app.
_LIVE_APP_PROFILES: dict[str, tuple[str, str]] = {
    "credit-memo-drafting": ("CREDIT_MEMO_PROFILE", "CREDIT_MEMO_LIVE_LLM_URL"),
    "cio-advisory": ("CIO_PROFILE", "CIO_LIVE_LLM_URL"),
    "trade-finance-checker": ("TRADE_FINANCE_PROFILE", "TRADE_FINANCE_LIVE_LLM_URL"),
    "compliance-advisory": ("COMPLIANCE_PROFILE", "COMPLIANCE_LIVE_LLM_URL"),
}
# Doc2's EDGAR traffic must be declared with a contact (SEC fair-access policy).
_EDGAR_CONTACT_ENV = "SEC_EDGAR_CONTACT"
_PRESENTER_STATE_DIR = _REPO_ROOT / "scripts" / "out" / "presenter-state"
_CDD_REVIEW_OUTBOX = _PRESENTER_STATE_DIR / "cdd-sow-research-review-outbox.sqlite3"
_REVIEW_CONSOLE_DB = _PRESENTER_STATE_DIR / "human-review-console-reviews.sqlite3"
_SQLITE_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


def _optional_setting(name: str) -> str:
    setting = read_env_setting(name)
    if setting.is_configured_empty:
        raise ValueError(
            f"{name} is set but empty; unset it when the launcher option is absent, "
            "or provide a non-empty value"
        )
    return setting.value


def _defaulted_setting(name: str, default: str) -> str:
    setting = read_env_setting(name)
    if setting.is_configured_empty:
        raise ValueError(
            f"{name} is set but empty; unset it to use {default!r}, or provide a non-empty value"
        )
    return setting.value or default


# app id -> sibling repo folder in the workspace.
_APP_REPOS: dict[str, str] = {
    "cdd-sow-research": "cdd-sow-research",
    "credit-memo-drafting": "credit-memo-drafting",
    "loan-document-intelligence": "loan-document-intelligence",
    "cio-advisory": "cio-advisory",
    "trade-finance-checker": "trade-finance-checker",
    "compliance-advisory": "compliance-advisory",
    "human-review-console": "human-review-console",
    "market-intelligence": "market-intelligence",
    "campaign-planner": "campaign-planner",
    "creative-studio": "creative-studio",
    "performance-marketing-optimisation": "performance-marketing-optimisation",
    "next-best-action": "next-best-action",
    "marketing-compliance-gate": "marketing-compliance-gate",
    "architecture-validator": "architecture-validator",
    "model-quality-gate": "model-quality-gate",
    "complaints-review": "complaints-review",
}

# app id -> the environment variable that app reads its profile from.
#
# Every app is TOLD its posture rather than left to inherit one. Only some of them refuse an
# inherited profile today, and relying on that is how the review console ended up refusing
# every write while its health check read green: the failure is invisible until a demo step
# tries to do something. Naming the profile for all of them costs one line each and removes
# the whole class.
_APP_PROFILE_ENVS: dict[str, str] = {
    "cdd-sow-research": "CDD_PROFILE",
    "credit-memo-drafting": "CREDIT_MEMO_PROFILE",
    "cio-advisory": "CIO_PROFILE",
    "trade-finance-checker": "TRADE_FINANCE_PROFILE",
    "loan-document-intelligence": "LOAN_DOC_PROFILE",
    "complaints-review": "COMPLAINTS_PROFILE",
    "compliance-advisory": "COMPLIANCE_PROFILE",
    "architecture-validator": "ARCH_VALIDATOR_PROFILE",
    "model-quality-gate": "AI_QUALITY_PROFILE",
    "human-review-console": "REVIEW_PROFILE",
    "market-intelligence": "MKT_INTEL_PROFILE",
    "campaign-planner": "MKT_CAMPAIGN_PROFILE",
    "creative-studio": "MKT_CREATIVE_PROFILE",
    "performance-marketing-optimisation": "MKT_PERF_PROFILE",
    "next-best-action": "MKT_NBA_PROFILE",
    "marketing-compliance-gate": "MKT_GOV_PROFILE",
}


def _port_of(url: str) -> int:
    parsed = urlparse(url)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def _backend_package(repo: Path) -> str | None:
    matches = sorted(repo.glob("src/*/api/app.py"))
    return matches[0].parent.parent.name if matches else None


def _backend_interpreter(repo: Path) -> str:
    """Return an app's own virtualenv interpreter, with an explicit safe fallback."""
    interpreter = repo / ".venv" / "bin" / "python"
    if interpreter.is_file() and os.access(interpreter, os.X_OK):
        return str(interpreter)
    print(
        f"  warning {repo.name}: no executable .venv/bin/python; "
        f"using portal interpreter {sys.executable}"
    )
    return sys.executable


def _selected_app_ids(catalog: JourneyCatalog, journeys: tuple[str, ...]) -> tuple[str, ...]:
    """Return selected apps in journey order, deduplicating future shared app mounts."""
    app_ids = (app_id for journey in journeys for app_id in catalog.journey(journey).app_ids)
    return tuple(dict.fromkeys(app_ids))


def _reset_presenter_state() -> tuple[Path, ...]:
    """Remove only launcher-owned synthetic review databases and their SQLite sidecars."""
    state_dir = _PRESENTER_STATE_DIR.resolve()
    databases = (_CDD_REVIEW_OUTBOX, _REVIEW_CONSOLE_DB)
    if any(database.resolve().parent != state_dir for database in databases):
        raise RuntimeError("presenter database path escaped the launcher-owned state directory")

    removed: list[Path] = []
    for database in databases:
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            candidate = Path(f"{database}{suffix}")
            if candidate.exists() or candidate.is_symlink():
                candidate.unlink()
                removed.append(candidate)
    return tuple(removed)


def _prepare_presenter_state() -> None:
    """Create the launcher-owned directory before either SQLite backend starts."""
    _PRESENTER_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _ui_environment(app_id: str) -> dict[str, str]:
    """Return the same-origin environment expected by an embedded app UI."""
    base_path = "/agent" if app_id == "cdd-sow-research" else f"/apps/{app_id}"
    environment = {
        "NEXT_PUBLIC_BASE_PATH": base_path,
        "NEXT_PUBLIC_API_BASE": f"{base_path}/api",
        "NEXT_PUBLIC_EMBED": "1",
        "NEXT_PUBLIC_FRAME_ANCESTORS": "'self'",
    }
    # Hrz7 predates the shared API variable and still consumes its specific name. Keep the
    # generic variable too so it can migrate without changing the production-shaped launcher.
    if app_id == "human-review-console":
        environment["NEXT_PUBLIC_REVIEW_API_URL"] = f"/apps/{app_id}/api"
    return environment


@dataclass(frozen=True, slots=True)
class _ReadinessCheck:
    label: str
    url: str
    # None when the check watches a service the launcher did not spawn (a reused,
    # already-running server): there is no child process to poll, only the URL to probe.
    process: subprocess.Popen[bytes] | None


class Launcher:
    def __init__(self, *, with_shells: bool, built: bool = False, live: bool = False) -> None:
        self.with_shells = with_shells
        self.built = built
        self.live = live
        self.procs: list[tuple[str, subprocess.Popen[bytes]]] = []
        self._readiness: list[_ReadinessCheck] = []
        self._startup_failures: dict[str, str] = {}
        self._stopped = False

    @staticmethod
    def _local_demo_s2s_token() -> str:
        """Return the launcher-only credential for the Doc1 -> Hrz7 service hop."""
        return _defaulted_setting(_LOCAL_DEMO_S2S_TOKEN_ENV, _DEFAULT_LOCAL_DEMO_S2S_TOKEN)

    @staticmethod
    def _doc1_security_environment() -> dict[str, str]:
        """Make Hrz9's native channel and identity choice explicit.

        This launcher is loopback-first, so it defaults to the acknowledged local persona.
        A hosted/IAP rehearsal opts in with JOURNEY_DOC1_IDENTITY_PROFILE=iap.
        """
        identity, _portal_profile = Launcher._doc1_identity_selection()
        environment = {
            "CDD_CHANNEL_PROFILE": "native",
            "CDD_IDENTITY_PROFILE": identity,
        }
        if identity == _DOC1_LOCAL_IDENTITY:
            environment[_INSECURE_DOC1_ACK_ENV] = "1"
        return environment

    @staticmethod
    def _doc1_identity_selection() -> tuple[str, str]:
        """Return one validated Doc1/portal identity-profile pair.

        The portal is the identity trust boundary for native embedding. A secure Doc1
        child therefore cannot run behind the portal's persona adapter, and a local
        Doc1 child cannot accidentally inherit the IAP portal adapter.
        """
        identity = _defaulted_setting(_DOC1_IDENTITY_ENV, _DOC1_LOCAL_IDENTITY)
        if identity not in {_DOC1_LOCAL_IDENTITY, _DOC1_HOSTED_IDENTITY}:
            raise ValueError(
                f"{_DOC1_IDENTITY_ENV} must be {_DOC1_LOCAL_IDENTITY!r} or "
                f"{_DOC1_HOSTED_IDENTITY!r}"
            )
        required_portal_profile = {
            _DOC1_LOCAL_IDENTITY: _PORTAL_LOCAL_PROFILE,
            _DOC1_HOSTED_IDENTITY: _PORTAL_HOSTED_PROFILE,
        }[identity]
        configured_portal_profile = _optional_setting(_PORTAL_PROFILE_ENV)
        if configured_portal_profile and configured_portal_profile != required_portal_profile:
            raise ValueError(
                f"{_DOC1_IDENTITY_ENV}={identity!r} requires "
                f"{_PORTAL_PROFILE_ENV}={required_portal_profile!r}; "
                f"got {configured_portal_profile!r}"
            )
        return identity, required_portal_profile

    @staticmethod
    def _live_doc1_environment() -> dict[str, str]:
        """Return the ``--live`` overrides for Doc1, letting an exported value win the defaults."""
        environment = {
            "CDD_PROFILE": _LIVE_DOC1_PROFILE,
            **{
                key: _defaulted_setting(key, default)
                for key, default in _LIVE_DOC1_DEFAULTS.items()
            },
        }
        # Never invented: without it the grounded lookups fail, and main() says so up front.
        project = _optional_setting(_GOOGLE_PROJECT_ENV)
        if project:
            environment[_GOOGLE_PROJECT_ENV] = project
        # The real synced watchlist snapshot, when the sibling repo has one (operator
        # export wins). Never pointed at the bundled fictional fixture: absence is
        # reported in the launch plan instead of silently degrading a live screen.
        exported = _optional_setting(_LIVE_SANCTIONS_ENV)
        snapshot = _WORKSPACE / _APP_REPOS["cdd-sow-research"] / _LIVE_SANCTIONS_SNAPSHOT
        if exported:
            environment[_LIVE_SANCTIONS_ENV] = exported
        elif snapshot.is_file():
            environment[_LIVE_SANCTIONS_ENV] = str(snapshot)
        return environment

    @staticmethod
    def _live_app_environment(app_id: str) -> dict[str, str]:
        """The ``--live`` overrides for a non-Doc1 journey app (operator exports win)."""
        if app_id == "loan-document-intelligence":
            # Doc5 has no hybrid live profile. Its managed extraction path is the
            # production-shaped real-data path; its local path stays credential-free.
            return {"LOAN_DOC_PROFILE": "gcp"}
        profile_env, llm_url_env = _LIVE_APP_PROFILES[app_id]
        environment = {profile_env: "live"}
        # One model server serves every app: mirror the resolved endpoint into the app's
        # own URL variable unless the operator already pinned that app elsewhere.
        model_url = _defaulted_setting(_MODEL_SERVER_URL_ENV, _DEFAULT_MODEL_SERVER_URL)
        environment[llm_url_env] = _defaulted_setting(llm_url_env, model_url)
        if app_id == "credit-memo-drafting":
            contact = _optional_setting(_EDGAR_CONTACT_ENV)
            if contact:
                environment[_EDGAR_CONTACT_ENV] = contact
        if app_id == "cio-advisory":
            # Grounded house-view research needs the project (like Doc1's research).
            project = _optional_setting(_GOOGLE_PROJECT_ENV)
            if project:
                environment[_GOOGLE_PROJECT_ENV] = project
        return environment

    def refresh_live_corpus(self) -> None:
        """Ingest/refresh Rsk1's REAL regulatory corpus before its backend starts.

        Idempotent and cheap when fresh: the refresh job re-fetches only expired or
        never-ingested sources (7-day TTL ledger), so a warm re-run is a series of
        ledger reads. A non-zero exit is a warning, not fatal: the JS-gated HKMA
        sources always fail headless fetch (they have a documented manual drop-box)
        while every directly fetchable instrument still ingests.
        """
        repo = _WORKSPACE / _APP_REPOS["compliance-advisory"]
        if not repo.is_dir():
            self._unavailable("rsk1-corpus", f"repo {repo.name} not found in the workspace")
            return
        print("  prep  rsk1-corpus            refreshing the real regulatory corpus")
        result = subprocess.run(  # noqa: S603 - fixed argv, launcher-owned interpreter
            [
                _backend_interpreter(repo),
                "-m",
                "compliance_advisory.pipelines.refresh_job",
                "--log-level",
                "WARNING",
            ],
            cwd=repo,
            env={**os.environ, "PYTHONPATH": "src"},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
            print(
                "  warning rsk1-corpus: some sources failed to refresh "
                "(JS-gated publishers fail headless fetch by design):"
            )
            for line in tail:
                print(f"    {line}")

    @staticmethod
    def _model_server_endpoint() -> tuple[int, str]:
        """(port, health_url) for the local model server Doc1's live profile calls.

        Doc1 posts to ``.../chat/completions``; the OpenAI-compatible MLX server answers a
        sibling ``/health``, so the health URL is derived from the endpoint's origin.
        """
        url = _defaulted_setting(_MODEL_SERVER_URL_ENV, _DEFAULT_MODEL_SERVER_URL)
        parsed = urlparse(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        origin = f"{parsed.scheme}://{parsed.hostname}:{port}"
        return port, f"{origin}/health"

    def launch_model_server(self) -> None:
        """Ensure the local model server is up before the live apps need it (``--live``).

        Reuse policy: a healthy listener already on the port is adopted as-is and never
        touched, because loading a multi-GB model takes minutes and the server is commonly
        managed outside this workspace (e.g. by launchd); the launcher must not restart a
        server it did not start. Only when nothing is listening does it run the operator's
        ``JOURNEY_MODEL_SERVER_CMD``; if that is unset the live plan already warned, and
        Doc1's model calls will fail fast rather than hang.
        """
        port, health_url = self._model_server_endpoint()
        if self._listener_pids(port):
            detail = self._probe(health_url)
            healthy = detail.startswith("HTTP ") and 200 <= int(detail.removeprefix("HTTP ")) < 400
            if healthy:
                print(f"  reuse model-server           already healthy on :{port} ({detail})")
                # Surface it in the readiness table without owning the process.
                self._readiness.append(
                    _ReadinessCheck(label="model-server", url=health_url, process=None)
                )
            else:
                # Not ours to replace, but flag it: something holds the port yet is not ready.
                self._unavailable(
                    "model-server",
                    f"a process holds :{port} but does not answer /health ({detail}); "
                    "Doc1's live model calls will fail until it is healthy",
                )
            return
        command = _optional_setting(_MODEL_SERVER_CMD_ENV)
        if not command:
            self._unavailable(
                "model-server",
                f"nothing on :{port} and {_MODEL_SERVER_CMD_ENV} is unset; export it (or start "
                "the model server yourself, see DEMO.md). Doc1's live model calls need it.",
            )
            return
        self._spawn(
            "model-server",
            shlex.split(command),
            cwd=_REPO_ROOT,
            env={},
            readiness_url=health_url,
        )

    def _spawn(
        self, label: str, cmd: list[str], *, cwd: Path, env: dict[str, str], readiness_url: str
    ) -> None:
        print(f"  start {label:22} {' '.join(cmd)}  (cwd={cwd})")
        # Do not inherit an operator-provided demo credential into browser-facing processes.
        # Explicit backend environments below selectively restore only the side of the S2S token
        # that each service needs.
        child_env = {
            key: value
            for key, value in os.environ.items()
            if key not in _S2S_SECRET_ENVIRONMENTS and key != _INSECURE_DOC1_ACK_ENV
        }
        child_env.update(env)
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=child_env,
            # npm and uvicorn can spawn a server child. A session per launcher entry makes the
            # whole descendant tree addressable by its process-group id during teardown.
            start_new_session=os.name == "posix",
        )
        self.procs.append((label, proc))
        self._readiness.append(_ReadinessCheck(label=label, url=readiness_url, process=proc))

    def _unavailable(self, label: str, reason: str) -> None:
        print(f"  skip  {label}: {reason}")
        self._startup_failures[label] = reason

    def _build_ui(self, app_id: str, ui_dir: Path, env: dict[str, str]) -> bool:
        """Build one embedded UI before starting its production server."""
        print(f"  build {app_id}-ui{'':17} npm run build  (cwd={ui_dir})")
        try:
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=str(ui_dir),
                env={**os.environ, **env},
                check=False,
            )
        except OSError as exc:
            self._unavailable(f"{app_id}-ui", f"could not run production build ({exc})")
            return False
        if result.returncode == 0:
            return True
        self._unavailable(f"{app_id}-ui", f"production build failed (exit {result.returncode})")
        return False

    @staticmethod
    def _listener_pids(port: int) -> tuple[int, ...]:
        """Return local TCP listeners for one port, if ``lsof`` is available."""
        try:
            output = subprocess.check_output(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return ()
        if not isinstance(output, str):
            return ()
        return tuple(int(pid) for pid in output.splitlines() if pid.isdecimal())

    @staticmethod
    def _process_cwd(pid: int) -> Path | None:
        """Resolve a listener's working directory without trusting its command line."""
        try:
            output = subprocess.check_output(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        for line in output.splitlines():
            if line.startswith("n"):
                return Path(line[1:]).resolve()
        return None

    def _clear_stale_listener(self, label: str, port: int, cwd: Path) -> bool:
        """Stop a prior launcher server on its dedicated port before a fresh built run.

        An old ``next start`` can otherwise satisfy readiness after the new process fails to bind,
        leaving the browser connected to stale assets.  Only listeners whose working directory is
        the expected demo repo are stopped; an unrelated service makes the launch fail closed.
        """
        pids = self._listener_pids(port)
        if not pids:
            return True

        expected_cwd = cwd.resolve()
        foreign = [pid for pid in pids if self._process_cwd(pid) != expected_cwd]
        if foreign:
            self._unavailable(
                label,
                f"port {port} is in use by a process outside {expected_cwd}",
            )
            return False

        print(f"  stop  stale {label:16} listener(s) on :{port}")
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not self._listener_pids(port):
                return True
            time.sleep(0.1)

        for pid in self._listener_pids(port):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(0.1)
        if not self._listener_pids(port):
            return True

        self._unavailable(label, f"could not stop stale listener on port {port}")
        return False

    def launch_app(self, app_id: str, api_port: int, ui_port: int) -> None:
        repo = _WORKSPACE / _APP_REPOS[app_id]
        if not repo.is_dir():
            self._unavailable(app_id, f"repo {repo.name} not found in the workspace")
            return
        package = _backend_package(repo)
        if package is None:
            self._unavailable(app_id, f"no src/*/api/app.py in {repo.name}")
            return
        ui_dir = repo / "ui"
        if self.built and not self._clear_stale_listener(f"{app_id}-backend", api_port, repo):
            return
        if (
            self.built
            and (ui_dir / "package.json").is_file()
            and not self._clear_stale_listener(f"{app_id}-ui", ui_port, ui_dir)
        ):
            return
        backend_env = {"PYTHONPATH": "src"}
        # Name the posture for every app before anything app-specific runs. The branches
        # below may raise it to a live profile; none of them may leave it inherited.
        profile_env = _APP_PROFILE_ENVS.get(app_id)
        if profile_env is not None:
            backend_env[profile_env] = _PORTAL_LOCAL_PROFILE
        # The CDD assessment must reach Hrz7 as a service producer, never by borrowing the
        # browser's portal identity.  This remains entirely local: both endpoints bind loopback
        # and the shared synthetic token is present only in their backend process environments.
        if app_id == "cdd-sow-research":
            backend_env.update(
                {
                    **self._doc1_security_environment(),
                    "CDD_HRZ7_URL": "http://127.0.0.1:8087",
                    "CDD_LOCAL_REVIEW_OUTBOX": str(_CDD_REVIEW_OUTBOX),
                    "CDD_S2S_TOKEN": self._local_demo_s2s_token(),
                }
            )
            if self.live:
                backend_env.update(self._live_doc1_environment())
        elif app_id in _LIVE_APP_PROFILES and self.live:
            backend_env.update(self._live_app_environment(app_id))
        elif app_id == "human-review-console":
            backend_env.update(
                {
                    "REVIEW_DB_PATH": str(_REVIEW_CONSOLE_DB),
                    "REVIEW_S2S_TOKEN": self._local_demo_s2s_token(),
                }
            )

        self._spawn(
            f"{app_id}-backend",
            [
                _backend_interpreter(repo),
                "-m",
                "uvicorn",
                f"{package}.api.app:app",
                "--port",
                str(api_port),
            ],
            cwd=repo,
            env=backend_env,
            readiness_url=f"http://127.0.0.1:{api_port}/healthz",
        )
        if (ui_dir / "package.json").is_file():
            ui_env = _ui_environment(app_id)
            if self.built and not self._build_ui(app_id, ui_dir, ui_env):
                return
            ui_readiness_path = ui_env["NEXT_PUBLIC_BASE_PATH"]
            self._spawn(
                f"{app_id}-ui",
                (
                    ["npm", "run", "start", "--", "--port", str(ui_port)]
                    if self.built
                    else ["npm", "run", "dev", "--", "--port", str(ui_port)]
                ),
                cwd=ui_dir,
                env=ui_env,
                readiness_url=f"http://127.0.0.1:{ui_port}{ui_readiness_path}",
            )
        else:
            self._unavailable(f"{app_id}-ui", f"no ui/ in {repo.name}")

    def launch_portal(self) -> None:
        if self.built and not self._clear_stale_listener("portal-bff", 8110, _REPO_ROOT):
            return
        _identity, portal_profile = self._doc1_identity_selection()
        portal_env = {
            "PYTHONPATH": "src",
            "PORTAL_FRAME_ANCESTORS": _SHELL_ORIGINS,
            _PORTAL_PROFILE_ENV: portal_profile,
        }
        if self.live:
            portal_env[_PORTAL_UPSTREAM_TIMEOUT_ENV] = _defaulted_setting(
                _PORTAL_UPSTREAM_TIMEOUT_ENV, _LIVE_PORTAL_UPSTREAM_TIMEOUT
            )
        self._spawn(
            "portal-bff",
            [sys.executable, "-m", "uvicorn", "journey_portal.api.app:app", "--port", "8110"],
            cwd=_REPO_ROOT,
            env=portal_env,
            readiness_url="http://127.0.0.1:8110/healthz",
        )

    def launch_shells(self, journeys: tuple[str, ...]) -> None:
        for journey in journeys:
            if journey == "ops":
                continue  # its own Angular shell, below
            port = _SHELL_PORTS.get(journey)
            if port is None:
                self._unavailable(f"{journey}-shell", f"no shell port is assigned to {journey!r}")
                continue
            if not (_REPO_ROOT / "ui-rm" / "node_modules").is_dir():
                self._unavailable(f"{journey}-shell", "run 'npm install' in ui-rm first")
                continue
            if self.built and not self._clear_stale_listener(
                f"{journey}-shell", port, _REPO_ROOT / "ui-rm"
            ):
                return
            # One codebase, one instance per journey. The shell renders whatever journey it
            # is named, so a new persona workbench is configuration rather than a second
            # front end; the separate build directory is what lets the instances coexist,
            # because concurrent runs sharing `.next` overwrite each other's output.
            self._spawn(
                f"{journey}-shell",
                ["npm", "run", "dev", "--", "--port", str(port)],
                cwd=_REPO_ROOT / "ui-rm",
                env={
                    "NEXT_PUBLIC_JOURNEY": journey,
                    "PORTAL_SHELL_DIST_DIR": f".next-{journey}",
                },
                readiness_url=f"http://127.0.0.1:{port}/",
            )
        if "ops" in journeys and (_REPO_ROOT / "ui-ops" / "node_modules").is_dir():
            if self.built and not self._clear_stale_listener(
                "ops-shell", 4200, _REPO_ROOT / "ui-ops"
            ):
                return
            self._spawn(
                "ops-shell",
                ["npm", "start", "--", "--host", "127.0.0.1"],
                cwd=_REPO_ROOT / "ui-ops",
                # Prevent Angular CLI's first-run analytics question from blocking a clean
                # presenter machine or mutating angular.json during launch.
                env={"NG_CLI_ANALYTICS": "false"},
                readiness_url="http://127.0.0.1:4200/",
            )
        elif "ops" in journeys:
            self._unavailable("ops-shell", "run 'npm install' in ui-ops first")

    def _probe(self, url: str) -> str | None:
        """Return a readiness detail, or a short error suitable for the status table."""
        try:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - fixed localhost URLs only
                status = response.status
            return f"HTTP {status}"
        except HTTPError as exc:
            return f"HTTP {exc.code}"
        except URLError as exc:
            return str(exc.reason)
        except OSError as exc:
            return str(exc)

    def wait_for_readiness(self, *, timeout: float, poll_interval: float = 0.5) -> bool:
        """Wait for every launched service and print the full readiness outcome."""
        pending = {check.label: check for check in self._readiness}
        ready: dict[str, str] = {}
        failed = dict(self._startup_failures)
        last_error: dict[str, str] = {}
        deadline = time.monotonic() + timeout

        while pending and time.monotonic() < deadline:
            for label, check in tuple(pending.items()):
                returncode = check.process.poll() if check.process is not None else None
                if returncode is not None:
                    failed[label] = f"exited with code {returncode} before ready"
                    del pending[label]
                    continue
                detail = self._probe(check.url)
                if detail.startswith("HTTP ") and 200 <= int(detail.removeprefix("HTTP ")) < 400:
                    ready[label] = detail
                    del pending[label]
                else:
                    last_error[label] = detail
            if pending:
                time.sleep(poll_interval)

        for label in pending:
            failed[label] = f"timed out after {timeout:g}s ({last_error.get(label, 'no response')})"

        print("\nreadiness:")
        for check in self._readiness:
            if check.label in ready:
                print(f"  {check.label:22} READY   {ready[check.label]}")
            else:
                print(f"  {check.label:22} FAILED  {failed[check.label]}")
        for label, reason in self._startup_failures.items():
            print(f"  {label:22} FAILED  {reason}")
        return not failed

    @staticmethod
    def _signal_process_group(proc: subprocess.Popen[bytes], signum: signal.Signals) -> None:
        """Signal a launcher child and all of its descendants on POSIX hosts."""
        if os.name == "posix":
            try:
                # ``start_new_session`` makes the child PID its process-group ID. This still
                # reaches npm/uvicorn descendants if their direct parent has already exited.
                os.killpg(proc.pid, signum)
            except ProcessLookupError:
                return
        elif proc.poll() is None:
            proc.send_signal(signum)

    def stop(self) -> None:
        """Stop all launcher-owned process trees, escalating after a graceful grace period."""
        if self._stopped:
            return
        self._stopped = True
        for _, proc in self.procs:
            self._signal_process_group(proc, signal.SIGTERM)

        deadline = time.monotonic() + 5
        for _, proc in self.procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=max(0, deadline - time.monotonic()))

        # A stopped npm wrapper can leave a Next.js child behind. Kill the whole group, including
        # a group whose leader has already exited, then reap the direct launcher children.
        for _, proc in self.procs:
            self._signal_process_group(proc, signal.SIGKILL)
        for _, proc in self.procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=1)

    def install_termination_handler(self) -> None:
        """Install cleanup before startup so an interrupted build/readiness poll cannot leak."""

        def _terminate(signum: int, frame: FrameType | None) -> None:
            print("\nstopping all processes...")
            self.stop()
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGINT, _terminate)
        signal.signal(signal.SIGTERM, _terminate)

    def wait(self, journeys: tuple[str, ...]) -> None:
        destinations = []
        if self.with_shells:
            for journey in journeys:
                port = _OPS_SHELL_PORT if journey == "ops" else _SHELL_PORTS.get(journey)
                if port is not None:
                    destinations.append(f"{journey} shell at http://localhost:{port}")
        if destinations:
            print(f"\ndemo ready. Open the {' and the '.join(destinations)}.")
            print("Ctrl-C to stop everything.\n")
        else:
            print("\ndemo ready. Ctrl-C to stop everything.\n")
        while True:
            time.sleep(1)


def _print_live_plan() -> None:
    """Report the ``--live`` overrides, including the warning a dry run must also show."""
    doc1_env = Launcher._live_doc1_environment()
    portal_timeout = _defaulted_setting(_PORTAL_UPSTREAM_TIMEOUT_ENV, _LIVE_PORTAL_UPSTREAM_TIMEOUT)
    port, health_url = Launcher._model_server_endpoint()
    if Launcher._listener_pids(port):
        print(f"  model server      reuse already-running server on :{port}")
    elif _optional_setting(_MODEL_SERVER_CMD_ENV):
        print(f"  model server      start via {_MODEL_SERVER_CMD_ENV} (nothing on :{port} yet)")
    else:
        print(
            f"  warning nothing on :{port} and {_MODEL_SERVER_CMD_ENV} is unset: Doc1's live "
            "generation and page transcription will fail. Start the model server, or export "
            f"{_MODEL_SERVER_CMD_ENV} so the launcher starts it (a cold model load needs a "
            "larger --readiness-timeout)."
        )
    print(f"  doc1 profile      {doc1_env['CDD_PROFILE']} (local model + grounded research)")
    print(f"  doc1 triage model {doc1_env['CDD_TRIAGE_MODEL']}")
    print(f"  doc1 body cap     {doc1_env['CDD_MAX_BODY_BYTES']} bytes")
    print(f"  portal proxy      {_PORTAL_UPSTREAM_TIMEOUT_ENV}={portal_timeout}s")
    if _GOOGLE_PROJECT_ENV in doc1_env:
        print(f"  google project    {doc1_env[_GOOGLE_PROJECT_ENV]}")
    else:
        print(
            f"  warning {_GOOGLE_PROJECT_ENV} is not set: Doc1's adverse-media and corporate "
            "registry grounding will fail. Export it before starting a live run."
        )
    if _LIVE_SANCTIONS_ENV in doc1_env:
        print(f"  doc1 watchlist    {doc1_env[_LIVE_SANCTIONS_ENV]}")
    else:
        print(
            f"  warning no real watchlist snapshot: screening would use Doc1's bundled "
            f"FICTIONAL fixture. Run scripts/sync_sanctions.py in {_APP_REPOS['cdd-sow-research']} "
            f"(writes {_LIVE_SANCTIONS_SNAPSHOT}) before a live run."
        )
    # Every other journey app runs its live profile too: real data in, no fictional seeds.
    for app_id in sorted(_LIVE_APP_PROFILES):
        print(f"  {app_id} profile      live")
    print("  rsk1 corpus       refreshed at startup (expired-only; real regulator sources)")
    if not _optional_setting(_EDGAR_CONTACT_ENV):
        print(
            f"  warning {_EDGAR_CONTACT_ENV} is not set: the SEC fair-access policy wants "
            "identified traffic; export it (an email) so Doc2's EDGAR grounding is not blocked."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the journey-portal demo.")
    parser.add_argument("--dry-run", action="store_true", help="Print the launch plan and exit.")
    parser.add_argument("--no-shells", action="store_true", help="Do not start the RM/Ops shells.")
    parser.add_argument(
        "--built",
        action="store_true",
        help="Build embedded UIs, then serve them with next start instead of next dev.",
    )
    parser.add_argument(
        "--fresh-state",
        action="store_true",
        help=(
            "Reset only the launcher's synthetic Doc1 review outbox and Hrz7 review queue "
            "before startup."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Run every journey app in its live profile: real data sources (uploads, SEC "
            "EDGAR, the real regulatory corpus, grounded research), one shared local model "
            "server, no fictional seeds; also raises the portal's upstream timeout."
        ),
    )
    parser.add_argument(
        "--journey",
        choices=("rm", "ops", "mkt", "gov", "svc"),
        help="Launch only one journey's embedded apps and matching shell.",
    )
    parser.add_argument(
        "--readiness-timeout",
        type=float,
        default=60,
        metavar="SECONDS",
        help="Seconds to wait for every backend, UI, BFF, and selected shell (default: 60).",
    )
    args = parser.parse_args()
    if args.readiness_timeout <= 0:
        parser.error("--readiness-timeout must be greater than zero")

    catalog = JourneyCatalog.from_mapping(load_journeys_mapping(Settings.load().journeys_path))
    selected_journeys = (args.journey,) if args.journey else tuple(catalog.journeys)
    selected_app_ids = _selected_app_ids(catalog, selected_journeys)
    plan = {
        app_id: (
            _port_of(catalog.app(app_id).api_upstream),
            _port_of(catalog.app(app_id).ui_upstream),
        )
        for app_id in selected_app_ids
    }

    print("Journey portal launch plan:")
    print(f"  portal BFF        :8110   ({len(selected_journeys)} journeys, {len(plan)} apps)")
    for app_id, (api_port, ui_port) in plan.items():
        repo_name = _APP_REPOS.get(app_id, "?")
        print(f"  {app_id:6} backend :{api_port}   ui :{ui_port}   repo {repo_name}")
    if not args.no_shells:
        for journey in selected_journeys:
            if journey == "ops":
                print(f"  ops shell (Angular) :{_OPS_SHELL_PORT}")
            elif journey in _SHELL_PORTS:
                print(f"  {journey} shell (React)   :{_SHELL_PORTS[journey]}")
    ui_mode = "built (next build + next start)" if args.built else "dev (next dev)"
    print(f"  embedded UIs      {ui_mode}")
    if args.fresh_state:
        print(f"  presenter state   reset requested ({_PRESENTER_STATE_DIR})")
    if args.live:
        _print_live_plan()
    if args.dry_run:
        return 0

    _prepare_presenter_state()
    if args.fresh_state:
        removed = _reset_presenter_state()
        if removed:
            print("\nreset synthetic presenter state:")
            for path in removed:
                print(f"  removed {path}")
        else:
            print("\nsynthetic presenter state already fresh")

    launcher = Launcher(with_shells=not args.no_shells, built=args.built, live=args.live)
    launcher.install_termination_handler()
    try:
        print("\nstarting processes:")
        # The model server (live only) starts first so a cold model load overlaps app startup.
        if args.live:
            launcher.launch_model_server()
            if "compliance-advisory" in plan:
                launcher.refresh_live_corpus()
        for app_id, (api_port, ui_port) in plan.items():
            launcher.launch_app(app_id, api_port, ui_port)
        launcher.launch_portal()
        if not args.no_shells:
            launcher.launch_shells(selected_journeys)
        if not launcher.wait_for_readiness(timeout=args.readiness_timeout):
            print("\ndemo failed readiness checks; stopping all processes.")
            return 1
        launcher.wait(selected_journeys)
        return 0
    finally:
        launcher.stop()


if __name__ == "__main__":
    raise SystemExit(main())
