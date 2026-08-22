from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from journey_portal.deployment_config import DeploymentConfig, DeploymentConfigError


@pytest.fixture
def runner_module(tmp_path: Path) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "deployment_config.py"
    spec = importlib.util.spec_from_file_location("deployment_config_runner_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._TERRAFORM_DIR = tmp_path
    module._GENERATED_INPUT = tmp_path / ".generated.tfvars.json"
    return module


def _config() -> DeploymentConfig:
    return DeploymentConfig(
        values={
            "DEPLOY_TERRAFORM_STATE_BUCKET": "bank-hrz9-prod-state",
            "DEPLOY_TERRAFORM_STATE_PREFIX": "hrz9/production",
        },
        secrets={"DEPLOY_IAP_OAUTH_CLIENT_SECRET": "secret"},
        terraform_inputs={},
    )


def test_rejects_competing_cli_and_files(runner_module: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(DeploymentConfigError, match="forbids"):
        runner_module._reject_competing_inputs(["plan", "-var=project_id=other"])

    (tmp_path / "production.auto.tfvars").write_text('project_id = "other"\n')
    with pytest.raises(DeploymentConfigError, match="competing"):
        runner_module._reject_competing_inputs(["plan"])


def test_verifies_exact_remote_backend_metadata(runner_module: ModuleType, tmp_path: Path) -> None:
    metadata = tmp_path / ".terraform" / "terraform.tfstate"
    metadata.parent.mkdir()
    metadata.write_text(
        json.dumps(
            {
                "backend": {
                    "type": "gcs",
                    "config": {
                        "bucket": "bank-hrz9-prod-state",
                        "prefix": "hrz9/production",
                    },
                }
            }
        )
    )
    runner_module._verify_remote_backend(_config())

    payload = json.loads(metadata.read_text())
    payload["backend"]["config"]["prefix"] = "other"
    metadata.write_text(json.dumps(payload))
    with pytest.raises(DeploymentConfigError, match="do not match"):
        runner_module._verify_remote_backend(_config())


def test_sanitizes_ambient_terraform_inputs(
    runner_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TF_VAR_project_id", "attacker")
    monkeypatch.setenv("TF_CLI_ARGS_plan", "-var project_id=attacker")
    monkeypatch.setenv("TF_DATA_DIR", "/tmp/attacker")
    monkeypatch.setenv("TF_WORKSPACE", "attacker")

    child = runner_module._sanitized_environment(_config())

    assert not any(
        key.startswith("TF_VAR_") for key in child if key != "TF_VAR_iap_oauth2_client_secret"
    )
    assert not any(key.startswith("TF_CLI_ARGS") for key in child)
    assert "TF_DATA_DIR" not in child
    assert "TF_WORKSPACE" not in child


@pytest.mark.parametrize(
    "arguments",
    [
        ["workspace", "new", "attacker"],
        ["state", "rm", "resource.name"],
        ["force-unlock", "123"],
        ["taint", "resource.name"],
        ["import", "resource.name", "id"],
        ["destroy"],
        ["plan", "-destroy", "-out=reviewed.tfplan"],
        ["plan", "-target=resource.name", "-out=reviewed.tfplan"],
        ["apply", "-auto-approve", "reviewed.tfplan"],
    ],
)
def test_strictly_rejects_unreviewed_commands_and_flags(
    runner_module: ModuleType, arguments: list[str]
) -> None:
    with pytest.raises(DeploymentConfigError, match="permits only"):
        runner_module._validate_terraform_args(arguments)


def test_saved_plan_integrity_binds_plan_inputs_and_backend(
    runner_module: ModuleType, tmp_path: Path
) -> None:
    runner_module._GENERATED_INPUT.write_text('{"project_id":"reviewed"}\n')
    plan = tmp_path / "reviewed.tfplan"
    plan.write_bytes(b"reviewed plan")
    runner_module._write_plan_integrity(_config(), plan)
    runner_module._verify_plan_integrity(_config(), plan)

    plan.write_bytes(b"changed plan")
    with pytest.raises(DeploymentConfigError, match="does not match"):
        runner_module._verify_plan_integrity(_config(), plan)


def test_requires_default_workspace(
    runner_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="other\n", stderr="")

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    with pytest.raises(DeploymentConfigError, match="default workspace"):
        runner_module._verify_default_workspace({})
