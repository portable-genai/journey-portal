from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def control_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_gcp_control_plane.py"
    spec = importlib.util.spec_from_file_location("verify_gcp_control_plane_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Runner:
    def __init__(
        self,
        *,
        iap_enabled: bool = True,
        unexpected_service: bool = False,
        bad_digest: bool = False,
    ) -> None:
        self.iap_enabled = iap_enabled
        self.unexpected_service = unexpected_service
        self.bad_digest = bad_digest
        self.calls: list[list[str]] = []

    def fetch(self, arguments: list[str]):
        self.calls.append(arguments)
        prefix = "hrz9-prod"
        if arguments[:3] == ["compute", "backend-services", "list"]:
            return [{"name": f"{prefix}-{name}"} for name in ("portal", "rm", "ops")]
        if arguments[:3] == ["run", "services", "list"]:
            names = [f"{prefix}-{name}" for name in ("portal", "rm", "ops")] + [
                f"{prefix}-{app}-{surface}"
                for app in ("doc1", "doc2", "doc3", "doc4", "doc5", "rsk1", "hrz7")
                for surface in ("ui", "api")
            ]
            if self.unexpected_service:
                names.append(f"{prefix}-unexpected")
            return [{"name": name} for name in names]
        name = arguments[3]
        if arguments[:3] == ["compute", "backend-services", "describe"]:
            return {
                "name": name,
                "iap": {"enabled": self.iap_enabled, "oauth2ClientId": "iap-client"},
                "loadBalancingScheme": "EXTERNAL_MANAGED",
                "protocol": "HTTP",
                "backends": [{"group": "neg"}],
            }
        return {
            "metadata": {
                "name": name,
                "labels": {"cloud.googleapis.com/location": "asia-southeast1"},
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "image": (
                                    _expected_images()[_component(name)]
                                    if not self.bad_digest
                                    else f"registry.internal/{name}@sha256:{'b' * 64}"
                                )
                            }
                        ]
                    }
                }
            },
        }


def _component(service_name: str) -> str:
    suffix = service_name.removeprefix("hrz9-prod-")
    return "bff" if suffix == "portal" else suffix


def _expected_images() -> dict[str, str]:
    components = ["bff", "rm", "ops"] + [
        f"{app}-{surface}"
        for app in ("doc1", "doc2", "doc3", "doc4", "doc5", "rsk1", "hrz7")
        for surface in ("ui", "api")
    ]
    return {
        component: f"registry.internal/{component}@sha256:{'a' * 64}" for component in components
    }


def _arguments() -> dict[str, str]:
    return {
        "project_id": "bank-hrz9-prod-001",
        "region": "asia-southeast1",
        "iap_client_id": "iap-client",
        "name_prefix": "hrz9-prod",
        "expected_images_json": json.dumps(_expected_images()),
    }


def test_verifies_exact_project_region_backends_and_services(control_module: ModuleType) -> None:
    runner = _Runner()

    control_module.verify(runner, **_arguments())

    assert len(runner.calls) == 22
    assert all("--project=bank-hrz9-prod-001" in call for call in runner.calls)
    assert all(
        "--region=asia-southeast1" in call
        for call in runner.calls
        if call[:3] == ["run", "services", "describe"]
    )


def test_rejects_disabled_iap(control_module: ModuleType) -> None:
    with pytest.raises(control_module.ControlPlaneError, match="reviewed IAP client"):
        control_module.verify(_Runner(iap_enabled=False), **_arguments())


def test_rejects_unexpected_prefixed_resource(control_module: ModuleType) -> None:
    with pytest.raises(control_module.ControlPlaneError, match="incomplete or unexpected"):
        control_module.verify(_Runner(unexpected_service=True), **_arguments())


def test_rejects_image_that_differs_from_reviewed_manifest(control_module: ModuleType) -> None:
    with pytest.raises(control_module.ControlPlaneError, match="exact reviewed image"):
        control_module.verify(_Runner(bad_digest=True), **_arguments())


def test_rejects_incomplete_expected_image_manifest(control_module: ModuleType) -> None:
    arguments = _arguments()
    arguments["expected_images_json"] = json.dumps({"bff": _expected_images()["bff"]})
    with pytest.raises(control_module.ControlPlaneError, match="exactly map"):
        control_module.verify(_Runner(), **arguments)
