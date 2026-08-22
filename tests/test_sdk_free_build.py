"""EVERY profile binds in an interpreter where the cloud SDKs cannot be imported.

Not "no SDK installed on this machine": ``tests/_sdk_free_probe.py`` installs a meta-path
finder that refuses the ``google`` and ``vertexai`` roots, then binds every port of the profile
in a fresh process. Proving the claim by absence would tie it to the developer machine: one
with the SDK installed would pass while hiding an eager import that breaks the offline gate
everywhere else.

Every profile, INCLUDING ``gcp`` and ``platform``, and that is the point rather than an
overreach. The IAP, KMS and Cloud Logging adapters import their SDK lazily, inside the method
that needs it, so they CONSTRUCT with no SDK present; that laziness is what lets a fork run the
offline portal without installing a cloud SDK at all, and AGENTS.md states it as a convention
("GCP imports stay lazy").

Nothing enforced it under prohibition. The parity suite binds all four profiles in the ordinary
interpreter, where an SDK that happens to be installed would satisfy a module-scope import and
say nothing, and the machines that run this gate have no cloud SDK, so absence was doing the
work. A ``from google.cloud import kms`` hoisted to module scope in a managed adapter is caught
here by construction rather than by luck, which is how it was proved: planted in the KMS
signing-key adapter it reddened ``gcp`` AND ``platform``, because the platform family reuses
that adapter, and left ``local`` and ``onprem`` green.

The parity suite proves the bindings conform to their Protocols in-process; this file proves
every one of them imports and constructs under prohibition.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from journey_portal.config import _BINDINGS, PROFILES

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every profile the binding tables bind, discovered rather than restated, so a profile added
#: to them is proved from the moment it exists instead of when somebody remembers this list.
ALL_PROFILES = tuple(sorted(set().union(*(set(table) for table in _BINDINGS.values()))))


def _run_probe(argument: str) -> subprocess.CompletedProcess[str]:
    # S603: the argv is this interpreter plus literals written above; ``argument`` is a
    # profile name discovered from the binding table, never caller input.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "tests._sdk_free_probe", argument],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": "src"},
        check=False,
        timeout=300,
    )


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_the_profile_binds_with_the_cloud_sdks_blocked(profile: str) -> None:
    completed = _run_probe(profile)
    assert completed.returncode == 0, (
        f"the {profile} profile could not be bound with the cloud SDKs blocked:\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    assert "no cloud SDK importable" in completed.stdout


def test_the_discovered_profiles_are_the_declared_runtime_profiles() -> None:
    """Discovery must keep finding everything the repository says it supports.

    A binding table that lost a profile, or an identity table that drifted from the port
    table, would otherwise shrink this suite in silence, and a suite that quietly proves less
    is the failure this whole file exists about.
    """
    assert set(ALL_PROFILES) == set(PROFILES)
    assert all(set(table) == set(ALL_PROFILES) for table in _BINDINGS.values())


def test_the_blocker_still_blocks() -> None:
    """A probe whose blocker quietly stopped blocking would make every proof above vacuous."""
    completed = _run_probe("--self-test")
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}"
    assert "blocker refused google.auth" in completed.stdout
