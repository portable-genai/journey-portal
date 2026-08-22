from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "npm_audit_gate.mjs"


def _run_gate(tmp_path: Path, payload: str, status: int = 0) -> subprocess.CompletedProcess[str]:
    fake_npm = tmp_path / "npm"
    fake_npm.write_text(
        f"#!/bin/sh\nprintf '%s' \"$FAKE_NPM_PAYLOAD\"\nexit {status}\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_NPM_PAYLOAD"] = payload
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is unavailable")
    return subprocess.run([node, str(GATE)], env=env, capture_output=True, text=True)


def _report(
    vulnerabilities: dict[str, object],
    *,
    counts_override: dict[str, int] | None = None,
) -> str:
    severities = ("info", "low", "moderate", "high", "critical")
    counts = {
        severity: sum(
            isinstance(finding, dict) and finding.get("severity") == severity
            for finding in vulnerabilities.values()
        )
        for severity in severities
    }
    counts["total"] = len(vulnerabilities)
    counts.update(counts_override or {})
    return json.dumps(
        {
            "vulnerabilities": vulnerabilities,
            "metadata": {"vulnerabilities": counts},
        }
    )


def test_accepts_complete_clean_schema(tmp_path: Path) -> None:
    assert _run_gate(tmp_path, _report({})).returncode == 0


@pytest.mark.parametrize("payload", ["", "not-json", "{}", '{"vulnerabilities":{}}'])
def test_rejects_empty_malformed_or_incomplete_schema(tmp_path: Path, payload: str) -> None:
    assert _run_gate(tmp_path, payload).returncode == 1


def test_rejects_high_findings_even_when_npm_returns_one(tmp_path: Path) -> None:
    payload = _report({"sharp": {"severity": "high"}})
    assert _run_gate(tmp_path, payload, status=1).returncode == 1


def test_rejects_metadata_only_high_or_inconsistent_counts(tmp_path: Path) -> None:
    metadata_only = _report({}, counts_override={"high": 1, "total": 1})
    assert _run_gate(tmp_path, metadata_only, status=1).returncode == 1

    inconsistent = _report(
        {"package": {"severity": "moderate"}},
        counts_override={"moderate": 0, "total": 0},
    )
    assert _run_gate(tmp_path, inconsistent, status=1).returncode == 1


def test_rejects_unexpected_npm_exit(tmp_path: Path) -> None:
    assert _run_gate(tmp_path, _report({}), status=2).returncode == 1
