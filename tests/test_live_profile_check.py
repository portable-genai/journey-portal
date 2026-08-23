from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def live_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "live_profile_check.py"
    spec = importlib.util.spec_from_file_location("live_profile_check_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Requester:
    def __init__(
        self,
        module: ModuleType,
        *,
        profile: str = "gcp",
        allow_unauthenticated: bool = False,
        broken_ui: str = "",
        wrong_membership: bool = False,
    ) -> None:
        self._module = module
        self._profile = profile
        self._allow_unauthenticated = allow_unauthenticated
        self._broken_ui = broken_ui
        self._wrong_membership = wrong_membership
        self.calls: list[tuple[str, str, str]] = []

    def get(self, base_url: str, path: str, token: str):
        self.calls.append((base_url, path, token))
        if not token:
            status = 200 if self._allow_unauthenticated else 403
            return self._module.Response(status, "text/html", b"denied")
        if path == "/":
            return self._module.Response(200, "text/html", b"<html></html>")
        if path == "/healthz":
            body = {"status": "ok", "profile": self._profile, "region": "asia-southeast1"}
        elif path == "/v1/whoami":
            body = {"subject": "live-user@bank.internal", "source": "gcp-iap"}
        elif path == "/v1/journeys":
            rm_ids = (
                ("doc1", "doc2", "doc3") if self._wrong_membership else ("doc1", "doc5", "doc3")
            )
            ops_ids = (
                ("doc3", "doc4", "rsk1", "hrz7")
                if self._wrong_membership
                else ("doc2", "doc4", "rsk1", "hrz7")
            )
            body = {
                "journeys": [
                    {
                        "key": "rm",
                        "apps": [
                            {"id": app_id, "ui_base": f"/apps/{app_id}/"} for app_id in rm_ids
                        ],
                    },
                    {
                        "key": "ops",
                        "apps": [
                            {"id": app_id, "ui_base": f"/apps/{app_id}/"} for app_id in ops_ids
                        ],
                    },
                ]
            }
        elif path == "/apps/doc5/api/healthz":
            body = {"status": "ok", "profile": "gcp", "region": "asia-southeast1"}
        elif path in {
            "/agent/api/healthz",
            "/apps/doc3/api/healthz",
            "/apps/doc2/api/healthz",
            "/apps/doc4/api/healthz",
            "/apps/rsk1/api/healthz",
        }:
            body = {"status": "ok", "profile": "live", "region": "asia-southeast1"}
        elif path == "/apps/hrz7/api/healthz":
            body = {"status": "ok", "profile": "gcp", "region": "asia-southeast1"}
        elif path == "/apps/doc1/":
            return self._module.Response(307, "text/html", b"", "/agent/")
        elif path == "/agent/":
            return self._ui("doc1", "/agent")
        elif re.fullmatch(r"/apps/(doc2|doc3|doc4|doc5|rsk1|hrz7)/", path):
            app_id = path.split("/")[2]
            return self._ui(app_id, f"/apps/{app_id}")
        elif path.endswith("/assets/app.js"):
            return self._module.Response(200, "application/javascript", b"console.log('ok')")
        else:
            raise AssertionError(path)
        return self._module.Response(200, "application/json", json.dumps(body).encode())

    def _ui(self, app_id: str, base_path: str):
        asset_base = "" if self._broken_ui == app_id else base_path
        body = (f'<html><script src="{asset_base}/assets/app.js"></script></html>').encode()
        return self._module.Response(200, "text/html", body)


def test_checks_both_hosts_through_iap(live_module: ModuleType) -> None:
    requester = _Requester(live_module)

    live_module.run_check(
        requester,
        rm_url="https://rm.bank.internal",
        ops_url="https://ops.bank.internal",
        token="signed-id-token",
        expected_region="asia-southeast1",
    )

    assert len(requester.calls) == 32
    assert sum(not call[2] for call in requester.calls) == 2
    assert all(call[2] == "signed-id-token" for call in requester.calls if call[2])
    app_health_paths = {path for _, path, _ in requester.calls if path.endswith("/api/healthz")}
    assert app_health_paths == {
        "/agent/api/healthz",
        "/apps/doc5/api/healthz",
        "/apps/doc3/api/healthz",
        "/apps/doc2/api/healthz",
        "/apps/doc4/api/healthz",
        "/apps/rsk1/api/healthz",
        "/apps/hrz7/api/healthz",
    }


def test_standalone_help_needs_only_the_system_python_stdlib() -> None:
    repo = Path(__file__).resolve().parents[1]
    # The interpreter UNDERNEATH the virtualenv, not `sys.executable`. Running the venv's own
    # python would import the venv's site-packages and the test would pass without proving
    # anything. `sys.base_prefix` is the base installation a venv was built from, so this is
    # the stdlib-only interpreter the test means, and it resolves wherever python is installed
    # rather than only at the path one distribution happens to use.
    system_python = Path(sys.base_prefix) / "bin" / "python3"
    result = subprocess.run(  # noqa: S603 - fixed system interpreter and repo-owned script
        [str(system_python), "scripts/live_profile_check.py", "--help"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--expected-region" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_workflow_token_preserves_all_three_states(
    live_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LIVE_IAP_ID_TOKEN", raising=False)
    assert live_module._live_iap_token() == ""
    monkeypatch.setenv("LIVE_IAP_ID_TOKEN", "  ")
    with pytest.raises(live_module.LiveCheckError, match="set but empty"):
        live_module._live_iap_token()
    monkeypatch.setenv("LIVE_IAP_ID_TOKEN", "  signed-token  ")
    assert live_module._live_iap_token() == "signed-token"


def test_fails_closed_on_placeholder_url(live_module: ModuleType) -> None:
    with pytest.raises(live_module.LiveCheckError, match="placeholder"):
        live_module.run_check(
            _Requester(live_module),
            rm_url="https://replace-me.example.test",
            ops_url="https://ops.bank.internal",
            token="signed-id-token",
            expected_region="asia-southeast1",
        )


def test_rejects_local_profile(live_module: ModuleType) -> None:
    with pytest.raises(live_module.LiveCheckError, match="managed profile"):
        live_module.run_check(
            _Requester(live_module, profile="local"),
            rm_url="https://rm.bank.internal",
            ops_url="https://ops.bank.internal",
            token="signed-id-token",
            expected_region="asia-southeast1",
        )


def test_rejects_unauthenticated_access(live_module: ModuleType) -> None:
    with pytest.raises(live_module.LiveCheckError, match="allowed unauthenticated access"):
        live_module.run_check(
            _Requester(live_module, allow_unauthenticated=True),
            rm_url="https://rm.bank.internal",
            ops_url="https://ops.bank.internal",
            token="signed-id-token",
            expected_region="asia-southeast1",
        )


def test_http_redirect_handler_refuses_to_forward(live_module: ModuleType) -> None:
    handler = live_module._RejectRedirects()

    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.invalid") is None


def test_rejects_local_embedded_app_profile(live_module: ModuleType) -> None:
    requester = _Requester(live_module)
    original_get = requester.get

    def get(base_url: str, path: str, token: str):
        response = original_get(base_url, path, token)
        if path == "/apps/doc2/api/healthz":
            body = {"status": "ok", "profile": "local", "region": "asia-southeast1"}
            return live_module.Response(200, "application/json", json.dumps(body).encode())
        return response

    requester.get = get
    with pytest.raises(live_module.LiveCheckError, match="doc2 health profile"):
        live_module.run_check(
            requester,
            rm_url="https://rm.bank.internal",
            ops_url="https://ops.bank.internal",
            token="signed-id-token",
            expected_region="asia-southeast1",
        )


def test_rejects_embedded_ui_asset_outside_build_base(live_module: ModuleType) -> None:
    with pytest.raises(live_module.LiveCheckError, match="escaped"):
        live_module.run_check(
            _Requester(live_module, broken_ui="doc2"),
            rm_url="https://rm.bank.internal",
            ops_url="https://ops.bank.internal",
            token="signed-id-token",
            expected_region="asia-southeast1",
        )


def test_rejects_apps_assigned_to_wrong_journey(live_module: ModuleType) -> None:
    with pytest.raises(live_module.LiveCheckError, match="journey membership"):
        live_module.run_check(
            _Requester(live_module, wrong_membership=True),
            rm_url="https://rm.bank.internal",
            ops_url="https://ops.bank.internal",
            token="signed-id-token",
            expected_region="asia-southeast1",
        )
