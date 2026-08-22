"""Offline unit coverage for the live journey smoke runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture(scope="module")
def smoke_module() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "smoke_journeys.py"
    spec = importlib.util.spec_from_file_location("smoke_journeys_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _PortalRecorder:
    def __init__(
        self,
        module: ModuleType,
        *,
        maker: str = "demo.approver@bank.example",
        profile: str = "local",
    ) -> None:
        self._module = module
        self._maker = maker
        self._profile = profile
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append((method, path, payload, headers))
        if path == "/v1/journeys":
            return self._module.JsonResponse(
                200,
                {
                    "journeys": [
                        {
                            "apps": [
                                {"id": "doc1", "api_base": "/agent/api"},
                                {"id": "doc5", "api_base": "/apps/doc5/api"},
                                {"id": "doc3", "api_base": "/apps/doc3/api"},
                            ]
                        },
                        {
                            "apps": [
                                {"id": "doc2", "api_base": "/apps/doc2/api"},
                                {"id": "doc4", "api_base": "/apps/doc4/api"},
                                {"id": "rsk1", "api_base": "/apps/rsk1/api"},
                                {"id": "hrz7", "api_base": "/apps/hrz7/api"},
                            ]
                        },
                    ]
                },
            )
        if path.endswith("/healthz"):
            return self._module.JsonResponse(200, {"status": "ok", "profile": self._profile})
        if path == "/v1/session/persona":
            return self._module.JsonResponse(
                200, {"persona": "approver", "subject": "demo.approver@bank.example"}
            )
        if path == "/apps/hrz7/api/v1/reviews":
            return self._module.JsonResponse(201, {"maker": self._maker})
        raise AssertionError(f"unexpected portal request: {method} {path}")


def test_smoke_uses_portal_paths_and_proves_selected_actor(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module)

    smoke_module.run_smoke(portal)

    paths = [path for _, path, _, _ in portal.calls]
    assert paths == [
        "/v1/journeys",
        "/agent/api/healthz",
        "/apps/doc5/api/healthz",
        "/apps/doc3/api/healthz",
        "/apps/doc2/api/healthz",
        "/apps/doc4/api/healthz",
        "/apps/rsk1/api/healthz",
        "/apps/hrz7/api/healthz",
        "/v1/session/persona",
        "/apps/hrz7/api/v1/reviews",
    ]
    assert all(path.startswith("/") for path in paths)
    assert portal.calls[-1][3] == {"X-Dev-Persona": "other-tenant"}


def test_smoke_fails_if_real_app_does_not_use_injected_actor(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module, maker="demo.other-tenant@bank.example")

    with pytest.raises(smoke_module.SmokeFailure, match="injected identity proof failed"):
        smoke_module.run_smoke(portal)


def test_smoke_accepts_a_live_profile(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module, profile="live")

    smoke_module.run_smoke(portal)

    assert [path for _, path, _, _ in portal.calls][0] == "/v1/journeys"


def test_smoke_rejects_any_other_profile(smoke_module: ModuleType) -> None:
    portal = _PortalRecorder(smoke_module, profile="gcp")

    with pytest.raises(smoke_module.SmokeFailure, match="received 'gcp'"):
        smoke_module.run_smoke(portal)


def test_portal_client_rejects_a_non_origin_base_url(smoke_module: ModuleType) -> None:
    with pytest.raises(ValueError, match="must be an origin"):
        smoke_module.PortalClient("http://127.0.0.1:8110/not-the-origin", timeout=1)
