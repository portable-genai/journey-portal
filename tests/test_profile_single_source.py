"""The profile has ONE source of truth, and it fails closed on an unset variable.

Mirrors Hrz7 (``human-review-console/tests/test_profile_single_source.py``) as the
standing gate for the absence-read-as-consent class. This repo is the reason the guard is worth
porting rather than trusting: ``api/app.py`` carried its own
``os.environ.get("PORTAL_PROFILE", "local")`` beside ``config.Settings.load``'s, and that copy was
the UNVALIDATED one, so a typo'd profile was rejected when settings loaded yet still selected
every ``local`` relaxation at the HTTP boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from journey_portal.config import (
    PROFILES,
    UNCONSENTED_PROFILE,
    Settings,
    resolve_profile,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "journey_portal"
_CONFIG = _SRC / "config.py"


def _python_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if p != _CONFIG)


def test_only_the_resolver_reads_the_profile_variable_from_the_environment() -> None:
    offenders = []
    for path in _python_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"(os\.environ|os\.getenv)[^\n]*PROFILE", line):
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "these modules re-derive the profile instead of calling config.resolve_profile, "
        "so an unset PORTAL_PROFILE can again be read as consent:\n" + "\n".join(offenders)
    )


def test_the_resolver_treats_an_absent_variable_as_no_choice() -> None:
    """UNSET is inherited-local-but-not-consented; the three states keep this distinct."""
    assert resolve_profile({}).explicit is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_set_but_empty_variable_is_a_boot_failure_not_the_unset_default(blank: str) -> None:
    """SET-AND-EMPTY expressed an intent that names no profile: fail closed, do not inherit."""
    with pytest.raises(ValueError, match="PORTAL_PROFILE"):
        resolve_profile({"PORTAL_PROFILE": blank})


def test_an_unconsented_run_is_not_the_local_profile_for_any_relaxation() -> None:
    choice = resolve_profile({})
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.exposure_profile != "local"
    assert UNCONSENTED_PROFILE not in PROFILES


def test_an_unconsented_run_still_binds_loopback() -> None:
    """The bind guard fails closed in the opposite direction: local is the restrictive case."""
    assert resolve_profile({}).bind_profile == "local"


def test_a_deliberate_profile_is_carried_through_unchanged() -> None:
    choice = resolve_profile({"PORTAL_PROFILE": "gcp"})
    assert (choice.profile, choice.explicit) == ("gcp", True)
    assert choice.exposure_profile == "gcp"
    assert choice.bind_profile == "gcp"


@pytest.mark.parametrize("bogus", ["typo", "Local", "GCP", "LOCAL"])
def test_an_unknown_or_miscapitalised_profile_is_refused_at_resolution(bogus: str) -> None:
    """Exact, case-sensitive: ``Local`` selects no relaxation but also no restriction."""
    with pytest.raises(ValueError, match="PORTAL_PROFILE"):
        resolve_profile({"PORTAL_PROFILE": bogus})


def test_directly_constructed_settings_are_a_deliberate_choice() -> None:
    """A caller who named the profile in code consented to it; only ``load`` can be unsure."""
    assert Settings(profile="local").choice.exposure_profile == "local"
    assert Settings(profile="local", profile_explicit=False).choice.exposure_profile != "local"
