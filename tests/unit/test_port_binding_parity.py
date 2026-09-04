"""Every port binds every profile, and every binding names a real port.

The audit that swept this fleet recorded journey-portal as carrying no parity test over its
bindings. That absence is the same shape as the defects this catalog has already paid
for: a port bound in settings but absent from the protocol map is unenforced with a
fully green build, and the build stays green precisely because nothing compares the two
sets.

The check is set equality in BOTH directions, deliberately. Checking only that every
port has an adapter would let an orphan adapter linger against a port that no longer
exists; checking only that every adapter names a port would let a new port ship with no
exit binding, which is the failure that quietly reaches for the managed stack. A
capability added in a later release cannot ship without a binding in every profile,
including the sovereign one.

Each assertion is paired with a proof that it would actually fire, because a guard
observed only passing is indistinguishable from a guard that asserts nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from journey_portal.config import PROFILES

_SRC = Path(__file__).resolve().parents[2] / "src" / "journey_portal"
_PORTS = _SRC / "ports"
_ADAPTERS = _SRC / "adapters"


def _module_names(directory: Path) -> set[str]:
    """The port-shaped modules in a directory, ignoring package plumbing."""
    return {
        path.stem
        for path in directory.glob("*.py")
        if path.stem != "__init__" and not path.stem.startswith("_")
    }


def _profile_directories() -> dict[str, Path]:
    return {profile: _ADAPTERS / profile for profile in sorted(PROFILES)}


def test_every_declared_profile_has_an_adapter_family() -> None:
    missing = [profile for profile, path in _profile_directories().items() if not path.is_dir()]
    assert not missing, (
        "these profiles are declared in config.PROFILES but have no adapter package, so "
        "selecting one fails at runtime rather than at startup: " + ", ".join(missing)
    )


def test_every_adapter_family_is_a_declared_profile() -> None:
    """An adapter directory nobody can select is dead weight that reads as coverage."""
    families = {
        path.name
        for path in _ADAPTERS.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    }
    undeclared = sorted(families - set(PROFILES))
    assert not undeclared, (
        "these adapter families are not in config.PROFILES, so they can never be "
        "selected and their presence overstates the binding coverage: " + ", ".join(undeclared)
    )


@pytest.mark.parametrize("profile", sorted(PROFILES))
def test_port_set_and_binding_set_are_equal_in_both_directions(profile: str) -> None:
    ports = _module_names(_PORTS)
    directory = _ADAPTERS / profile
    assert directory.is_dir(), f"no adapter family for declared profile {profile!r}"
    bindings = _module_names(directory)

    unbound = sorted(ports - bindings)
    orphaned = sorted(bindings - ports)

    assert not unbound, (
        f"profile {profile!r} declares no binding for these ports, so a capability can "
        "ship without an exit implementation while the build stays green: " + ", ".join(unbound)
    )
    assert not orphaned, (
        f"profile {profile!r} binds these modules to no registered port, so the binding "
        "map claims coverage the protocol map does not define: " + ", ".join(orphaned)
    )


def test_the_parity_comparison_would_actually_fail_on_a_missing_binding() -> None:
    """Prove the set comparison fires, rather than trusting that it would."""
    ports = _module_names(_PORTS)
    assert ports, "no port modules were discovered, so the guard above compares nothing"

    pretend_bindings = set(ports)
    dropped = sorted(ports)[0]
    pretend_bindings.remove(dropped)

    assert ports - pretend_bindings == {dropped}, (
        "the comparison used by the parity test did not detect a removed binding, which "
        "means the green result above proves nothing"
    )


def test_the_parity_comparison_would_actually_fail_on_an_orphan_binding() -> None:
    ports = _module_names(_PORTS)
    pretend_bindings = set(ports) | {"a_port_that_does_not_exist"}

    assert pretend_bindings - ports == {"a_port_that_does_not_exist"}, (
        "the comparison used by the parity test did not detect an orphan binding"
    )
