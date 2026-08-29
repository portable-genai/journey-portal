"""Bind every port of one profile in a FRESH interpreter with the cloud SDKs blocked.

Run as ``python -m tests._sdk_free_probe <profile>`` from the repo root (with ``src``
on ``PYTHONPATH``); exits 0 when every port binding of that profile imported and constructed,
non-zero naming the offence otherwise. ``--self-test`` proves the blocker itself still refuses,
because a probe whose blocker quietly stopped blocking would pass on any machine and prove
nothing.

Why a subprocess rather than a fixture that unloads modules in place: reloading rebinds the
adapter classes, so every already-imported test module would keep stale class objects and
``isinstance`` checks elsewhere in the suite would start failing for reasons that have nothing
to do with the code under test. A fresh process also proves the stronger claim: a whole
interpreter in which the SDK was never importable, which is the difference between "no SDK
installed on this machine" and "cannot be imported".

This repository has no separate parity-suite settings helper: the binding table
(:data:`journey_portal.config._BINDINGS`) and the container that reads it ARE the wiring, and
``tests/test_contract_parity.py`` drives the same two objects. So the probe builds the real
container and touches every port the table names, rather than restating a construction loop
that could drift from the one that ships. The container asserts Protocol conformance as it
binds, so a port that constructs into the wrong shape is caught here too.

The settings carry the same ephemeral inputs the parity suite passes (a temporary audit
database and signing-key file, a synthetic HMAC key), because several adapters read them while
constructing and a probe that could not build them would prove nothing about the rest.
"""

from __future__ import annotations

import importlib
import sys

#: Import roots every profile must BIND without. ``google`` covers every ``google-cloud-*``
#: distribution plus ``google-genai`` and ``google-adk``; ``vertexai`` ships as its own root.
BLOCKED_ROOTS = ("google", "vertexai")


class _BlockedSdkFinder:
    """A meta-path finder that refuses the blocked roots, whatever is installed."""

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if any(fullname == root or fullname.startswith(root + ".") for root in BLOCKED_ROOTS):
            raise ModuleNotFoundError(
                f"{fullname} is blocked: this profile must bind with no cloud SDK"
            )
        return None


def _install_blocker() -> None:
    evicted = [
        name
        for name in sys.modules
        if any(name == root or name.startswith(root + ".") for root in BLOCKED_ROOTS)
    ]
    for name in evicted:
        del sys.modules[name]
    sys.meta_path.insert(0, _BlockedSdkFinder())  # type: ignore[arg-type]


def _self_test() -> int:
    """The blocker must refuse, and refuse for the right reason."""
    _install_blocker()
    try:
        importlib.import_module("google.auth")
    except ModuleNotFoundError as exc:
        if "blocked" in str(exc):
            print("blocker refused google.auth")
            return 0
        print(f"google.auth failed, but not because of the blocker: {exc}", file=sys.stderr)
        return 1
    print("google.auth imported despite the blocker", file=sys.stderr)
    return 1


def main(profile: str) -> int:
    _install_blocker()

    # Imported AFTER the blocker is installed, so an eager SDK import anywhere on the
    # binding path, config.py included, is caught rather than arriving pre-loaded.
    import tempfile
    from pathlib import Path

    from journey_portal.config import _BINDINGS, Settings, build_container

    scratch = Path(tempfile.mkdtemp(prefix="sdk-free-probe-"))
    container = build_container(
        Settings(
            profile=profile,
            local_audit_db=str(scratch / "audit.sqlite3"),
            audit_hmac_key="k" * 32,
            observability_url="http://localhost:8085",
            observability_audience="https://observability-audience.example.test",
            bff_signing_key_file=str(scratch / "bff-signing-key.json"),
        )
    )
    # Every port the binding table names. Enumerated from the table rather than listed here, so
    # a port added to the hexagon is proved on arrival instead of when somebody remembers this
    # file.
    ports = sorted(_BINDINGS)
    for port in ports:
        getattr(container, port)
    if not ports:
        print("the binding table names no ports; a build of nothing proves nothing")
        return 1
    print(f"{profile}: bound {len(ports)} ports with no cloud SDK importable")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "usage: python -m tests._sdk_free_probe <profile>|--self-test",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(_self_test() if sys.argv[1] == "--self-test" else main(sys.argv[1]))
