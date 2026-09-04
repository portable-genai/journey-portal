#!/usr/bin/env python3
"""Validate or render the named journey-portal deployment environment contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from journey_portal.deployment_config import (
    DeploymentConfig,
    DeploymentConfigError,
    load_deployment_config,
    write_terraform_inputs,
)

_REPO = Path(__file__).resolve().parents[1]
_TERRAFORM_DIR = _REPO / "infra" / "terraform"
_GENERATED_INPUT = _TERRAFORM_DIR / ".generated.tfvars.json"
_PLAN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.tfplan$")
_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=_REPO / ".env")
    parser.add_argument("--secrets-file", type=Path, default=_REPO / ".env.secrets")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="validate without writing or running Terraform")
    subparsers.add_parser("render", help="write non-secret Terraform inputs")
    terraform = subparsers.add_parser(
        "terraform",
        help="validate, render, and run Terraform with the secret supplied only in process env",
    )
    terraform.add_argument("terraform_args", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def _reject_competing_inputs(arguments: list[str]) -> None:
    for argument in arguments:
        if any(
            argument == prefix or argument.startswith(prefix + "=")
            for prefix in (
                "-var",
                "-var-file",
                "-backend-config",
                "-backend=false",
                "-state",
                "-chdir",
            )
        ):
            raise DeploymentConfigError(f"reviewed Terraform runner forbids {argument!r}")
    forbidden = [
        path
        for path in _TERRAFORM_DIR.iterdir()
        if (
            path.name
            in {"terraform.tfvars", "terraform.tfvars.json", "override.tf", "override.tf.json"}
            or path.name.endswith(".auto.tfvars")
            or path.name.endswith(".auto.tfvars.json")
            or path.name.endswith("_override.tf")
            or path.name.endswith("_override.tf.json")
        )
    ]
    if forbidden:
        raise DeploymentConfigError(
            "remove competing Terraform input files: "
            + ", ".join(sorted(path.name for path in forbidden))
        )
    local_state = sorted(_TERRAFORM_DIR.glob("terraform.tfstate*"))
    if local_state:
        raise DeploymentConfigError(
            "local Terraform state is forbidden: " + ", ".join(path.name for path in local_state)
        )


def _safe_plan_path(raw_name: str) -> Path:
    if not _PLAN_NAME_RE.fullmatch(raw_name) or Path(raw_name).name != raw_name:
        raise DeploymentConfigError("saved plan must be a .tfplan basename in infra/terraform")
    return _TERRAFORM_DIR / raw_name


def _validate_terraform_args(arguments: list[str]) -> tuple[str, Path | None]:
    """Accept only the reviewed, non-mutating Terraform workflow."""

    if not arguments:
        raise DeploymentConfigError("terraform requires arguments after --")
    subcommand = arguments[0]
    if subcommand == "init" and arguments == ["init"]:
        return subcommand, None
    if subcommand == "validate" and arguments == ["validate"]:
        return subcommand, None
    if subcommand == "plan" and len(arguments) == 2 and arguments[1].startswith("-out="):
        return subcommand, _safe_plan_path(arguments[1].removeprefix("-out="))
    if subcommand == "apply" and len(arguments) == 2:
        return subcommand, _safe_plan_path(arguments[1])
    if subcommand == "show" and len(arguments) == 3 and arguments[1] == "-json":
        return subcommand, _safe_plan_path(arguments[2])
    if subcommand == "output":
        if arguments == ["output"] or arguments == ["output", "-json"]:
            return subcommand, None
        if len(arguments) == 2 and _OUTPUT_NAME_RE.fullmatch(arguments[1]):
            return subcommand, None
        if (
            len(arguments) == 3
            and arguments[1] in {"-json", "-raw"}
            and _OUTPUT_NAME_RE.fullmatch(arguments[2])
        ):
            return subcommand, None
    raise DeploymentConfigError(
        "reviewed Terraform runner permits only init, validate, "
        "plan -out=<name>.tfplan, apply <name>.tfplan, "
        "show -json <name>.tfplan, and read-only output"
    )


def _verify_remote_backend(config: DeploymentConfig) -> None:
    metadata_path = _TERRAFORM_DIR / ".terraform" / "terraform.tfstate"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentConfigError(
            "Terraform backend metadata is missing; run reviewed terraform init first"
        ) from exc
    if not isinstance(metadata, dict):
        raise DeploymentConfigError("Terraform backend metadata is malformed")
    backend = metadata.get("backend", {})
    if not isinstance(backend, dict):
        raise DeploymentConfigError("Terraform backend metadata is malformed")
    expected_bucket = config.values["DEPLOY_TERRAFORM_STATE_BUCKET"]
    expected_prefix = config.values["DEPLOY_TERRAFORM_STATE_PREFIX"]
    if backend.get("type") != "gcs":
        raise DeploymentConfigError("Terraform backend must be GCS")
    backend_config = backend.get("config", {})
    if not isinstance(backend_config, dict):
        raise DeploymentConfigError("Terraform backend metadata is malformed")
    if (
        backend_config.get("bucket") != expected_bucket
        or backend_config.get("prefix") != expected_prefix
    ):
        raise DeploymentConfigError("Terraform backend bucket/prefix do not match reviewed inputs")


def _sha256(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            raise DeploymentConfigError(f"reviewed artifact is missing or unsafe: {path.name}")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DeploymentConfigError(f"cannot read reviewed artifact: {path.name}") from exc


def _integrity_path(plan_path: Path) -> Path:
    return plan_path.with_name(f"{plan_path.name}.integrity.json")


def _plan_integrity(config: DeploymentConfig, plan_path: Path) -> dict[str, object]:
    return {
        "version": 1,
        "plan_sha256": _sha256(plan_path),
        "inputs_sha256": _sha256(_GENERATED_INPUT),
        "backend": {
            "bucket": config.values["DEPLOY_TERRAFORM_STATE_BUCKET"],
            "prefix": config.values["DEPLOY_TERRAFORM_STATE_PREFIX"],
        },
    }


def _write_plan_integrity(config: DeploymentConfig, plan_path: Path) -> None:
    integrity_path = _integrity_path(plan_path)
    if integrity_path.is_symlink():
        raise DeploymentConfigError(f"reviewed artifact is unsafe: {integrity_path.name}")
    integrity_path.write_text(
        json.dumps(_plan_integrity(config, plan_path), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _verify_plan_integrity(config: DeploymentConfig, plan_path: Path) -> None:
    integrity_path = _integrity_path(plan_path)
    try:
        if integrity_path.is_symlink():
            raise DeploymentConfigError(f"reviewed artifact is unsafe: {integrity_path.name}")
        recorded = json.loads(integrity_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeploymentConfigError(
            f"saved plan integrity record is missing or malformed: {integrity_path.name}"
        ) from exc
    if recorded != _plan_integrity(config, plan_path):
        raise DeploymentConfigError(
            "saved plan does not match the reviewed plan, inputs, and backend"
        )


def _sanitized_environment(config: DeploymentConfig) -> dict[str, str]:
    child = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TF_VAR_") and not key.startswith("TF_CLI_ARGS")
    }
    child.pop("TF_DATA_DIR", None)
    child.pop("TF_WORKSPACE", None)
    child["TF_VAR_iap_oauth2_client_secret"] = config.secrets["DEPLOY_IAP_OAUTH_CLIENT_SECRET"]
    return child


def _verify_default_workspace(environment: dict[str, str]) -> None:
    try:
        result = subprocess.run(
            ["terraform", f"-chdir={_TERRAFORM_DIR}", "workspace", "show"],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise DeploymentConfigError("could not inspect the active Terraform workspace") from exc
    if result.returncode != 0 or result.stdout.strip() != "default":
        raise DeploymentConfigError("reviewed Terraform runner requires the default workspace")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_deployment_config(args.env_file, args.secrets_file)
        if args.command == "check":
            print("PASS named deployment configuration is complete and contains no placeholders")
            return 0
        if args.command == "render":
            write_terraform_inputs(config, _GENERATED_INPUT)
            print(f"PASS wrote non-secret Terraform inputs to {_GENERATED_INPUT}")
            return 0
        terraform_args = list(args.terraform_args)
        if terraform_args and terraform_args[0] == "--":
            terraform_args = terraform_args[1:]
        subcommand, plan_path = _validate_terraform_args(terraform_args)
        _reject_competing_inputs(terraform_args)
        write_terraform_inputs(config, _GENERATED_INPUT)
        print(f"PASS wrote non-secret Terraform inputs to {_GENERATED_INPUT}")
        environment = _sanitized_environment(config)
        command = ["terraform", f"-chdir={_TERRAFORM_DIR}", *terraform_args]
        if subcommand == "init":
            command.extend(
                [
                    "-reconfigure",
                    f"-backend-config=bucket={config.values['DEPLOY_TERRAFORM_STATE_BUCKET']}",
                    f"-backend-config=prefix={config.values['DEPLOY_TERRAFORM_STATE_PREFIX']}",
                ]
            )
        else:
            _verify_remote_backend(config)
            _verify_default_workspace(environment)
            if subcommand == "plan":
                command.append(f"-var-file={_GENERATED_INPUT}")
            if subcommand in {"apply", "show"}:
                assert plan_path is not None
                _verify_plan_integrity(config, plan_path)
        result = subprocess.run(command, env=environment, check=False)
        if result.returncode == 0 and subcommand == "init":
            _verify_remote_backend(config)
            _verify_default_workspace(environment)
        if result.returncode == 0 and subcommand == "plan":
            assert plan_path is not None
            _write_plan_integrity(config, plan_path)
        return result.returncode
    except (DeploymentConfigError, OSError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
