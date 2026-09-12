"""Where the coverage sweep looks for each persona shell, and what it refuses to guess.

``e2e/app_coverage.py`` walks every journey the target's own ``/v1/journeys`` declares and opens
each one's shell. The sweep is therefore the only thing that holds a HOSTNAME to the JOURNEY it
renders: Terraform records a shell's journey as the operator's claim and cannot read it back out
of an image digest, and the React shell falls back to the first journey in the catalog when the
one it was built for is absent -- a healthy page under the wrong persona's name.

These cases cover the resolution rules for a deployment that publishes one host per persona,
including the third one added on 2026-09-12.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_CATALOG_JOURNEYS = ("rm", "ops", "mkt", "gov", "svc", "risk")


@pytest.fixture(scope="module")
def targets() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "e2e" / "targets.py"
    spec = importlib.util.spec_from_file_location("e2e_targets_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _cloud(targets: ModuleType, base_url: str = "https://rm-1-2-3-4.nip.io") -> object:
    """A resolved cloud target, without minting a token: resolution is what is under test."""
    return targets.Target(
        name=targets.GCP, base_url=base_url, headers={"Authorization": "Bearer x"}
    )


def test_a_named_marketing_origin_is_driven_on_the_deployment(
    targets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    for journey in _CATALOG_JOURNEYS:
        monkeypatch.delenv(f"PORTAL_E2E_SHELL_{journey.upper()}_BASE_URL", raising=False)
    monkeypatch.delenv("PORTAL_E2E_SHELLS", raising=False)
    monkeypatch.setenv("PORTAL_E2E_SHELL_OPS_BASE_URL", "https://ops-1-2-3-4.nip.io")
    monkeypatch.setenv("PORTAL_E2E_SHELL_MKT_BASE_URL", "https://mkt-1-2-3-4.nip.io/")

    drivable, undriven = targets.shells(_cloud(targets), ("rm", "ops", "mkt"))

    by_journey = {shell.journey: shell for shell in drivable}
    assert set(by_journey) == {"rm", "ops", "mkt"}
    assert undriven == ()
    # The trailing slash is stripped so every path the sweep joins is built the same way.
    assert by_journey["mkt"].origin == "https://mkt-1-2-3-4.nip.io"
    assert by_journey["mkt"].source == "named"
    # The RM origin stays PORTAL_E2E_BASE_URL, which is the contract the deep journey shares.
    assert by_journey["rm"].source == "base-url"


def test_a_marketing_shell_with_no_named_origin_is_reported_undriven_not_passed(
    targets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never opened and perfectly fine must not print the same for a persona host."""
    for journey in _CATALOG_JOURNEYS:
        monkeypatch.delenv(f"PORTAL_E2E_SHELL_{journey.upper()}_BASE_URL", raising=False)
    monkeypatch.delenv("PORTAL_E2E_SHELLS", raising=False)

    drivable, undriven = targets.shells(_cloud(targets), ("rm", "ops", "mkt"))

    assert [shell.journey for shell in drivable] == ["rm"]
    assert undriven == ("ops", "mkt")


def test_a_blank_marketing_origin_refuses_rather_than_falling_back(
    targets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three states, never two: a template that renders nothing is an error, not a default."""
    monkeypatch.delenv("PORTAL_E2E_SHELLS", raising=False)
    monkeypatch.setenv("PORTAL_E2E_SHELL_MKT_BASE_URL", "  ")

    with pytest.raises(targets.TargetError, match="PORTAL_E2E_SHELL_MKT_BASE_URL"):
        targets.shells(_cloud(targets), ("rm", "mkt"))


def test_a_laptop_run_takes_the_launcher_port_for_the_marketing_shell(
    targets: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    for journey in _CATALOG_JOURNEYS:
        monkeypatch.delenv(f"PORTAL_E2E_SHELL_{journey.upper()}_BASE_URL", raising=False)
    monkeypatch.delenv("PORTAL_E2E_SHELLS", raising=False)

    drivable, undriven = targets.shells(
        targets.Target(name=targets.LOCAL, base_url="http://localhost:3000"), ("rm", "mkt")
    )

    by_journey = {shell.journey: shell for shell in drivable}
    assert by_journey["mkt"].origin == "http://localhost:3001"
    assert by_journey["mkt"].source == "launcher-port"
    assert undriven == ()
