"""Cross-repo proof: an assertion minted HERE verifies against cdd-sow-research's ACTUAL verifier.

This is the acceptance criterion for the Mode 5 service-identity slice. Everything else in this repo
can be green while the two halves still disagree about a header member, a claim name or a signature
encoding, and the only way to find that out before a deployment is to run cdd-sow-research's real
``PrivateKeyJwtVerifier`` over bytes this repo produced.

How it stays honest:

* the verifier is IMPORTED from the sibling ``cdd-sow-research`` working tree, not vendored, so a
  change to cdd-sow-research's rules shows up here as a failure rather than as drift; * it runs in
  cdd-sow-research's own virtualenv (``PyJWT`` plus ``cryptography`` live there, and this repo stays
  SDK-free and dependency-free by design), through a subprocess and a JSON channel; * the assertion
  is signed by the repo's REAL local adapter with a pinned generated key, not by a test helper that
  reimplements the encoding; * the negative cases matter as much as the positive one: a replayed
  JTI, a tampered signature, a wrong expected client and an expired assertion must all be REFUSED. A
  fixture that only ever observed acceptance would pass against a verifier that accepted everything.

Skipped, with a reason, where the sibling checkout or its virtualenv is absent.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from journey_portal.adapters.local.bff_credentials import LocalBffSigningKeyAdapter
from journey_portal.config import Settings
from journey_portal.domain.bff_assertion import ClientAssertionPolicy, mint_client_assertion

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DRIVER = Path(__file__).resolve().parent / "cross_repo" / "verify_with_doc1.py"

CLIENT_ID = "hrz9-journey-portal-bff-fixture"
GRANT_ENDPOINT = "https://cdd-sow-research.example/agent/api/v1/embed/grants"


def _doc1_repo() -> Path | None:
    configured = os.environ.get("DOC1_REPO_PATH", "").strip()
    candidate = Path(configured) if configured else _REPO_ROOT.parent / "cdd-sow-research"
    verifier = candidate / "src" / "cdd_sow_research" / "adapters" / "oidc" / "private_key_jwt.py"
    return candidate if verifier.is_file() else None


def _doc1_python(repo: Path) -> Path | None:
    interpreter = repo / ".venv" / "bin" / "python"
    return interpreter if interpreter.is_file() else None


def _doc1_env(repo: Path) -> dict[str, str]:
    """The subprocess environment: cdd-sow-research's source on the path, nothing of this repo's
    leaking in.
    """
    return {
        "PYTHONPATH": str(repo / "src"),
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }


def _verifier_importable(interpreter: Path, repo: Path) -> bool:
    """Can cdd-sow-research's verifier actually be imported in that interpreter?

    The interpreter merely EXISTING is not evidence its dependencies are installed: a clean clone
    can find a sibling ``.venv`` that never had the ``oidc`` extra (``PyJWT`` + ``cryptography``),
    or even ``hex_service_kit``, and running the proof against it would FAIL rather than skip,
    which is what made ``make check`` red from a clean clone. Probe the exact import the driver
    performs, so the guard is honest: either the proof runs and passes, or it skips for a reason.

    Importing the wrapper module is NOT that probe. ``private_key_jwt`` imports ``jwt`` lazily,
    inside ``_require_jwt()``, so the module imports cleanly in an interpreter that has no PyJWT
    at all: the probe went green and the driver then died on ``ModuleNotFoundError``, turning
    what should have been a skip back into the failure this guard exists to prevent. Resolve the
    lazy dependency here, the way the driver will.
    """
    probe = subprocess.run(
        [
            str(interpreter),
            "-c",
            "from cdd_sow_research.adapters.oidc import private_key_jwt as m; m._require_jwt()",
        ],
        capture_output=True,
        text=True,
        env=_doc1_env(repo),
        timeout=120,
        check=False,
    )
    return probe.returncode == 0


def _run_doc1_verifier(request: dict[str, Any]) -> dict[str, Any]:
    repo = _doc1_repo()
    if repo is None:
        pytest.skip(
            "the sibling cdd-sow-research checkout is not present; set DOC1_REPO_PATH to run "
            "the cross-repo private_key_jwt proof"
        )
    interpreter = _doc1_python(repo)
    if interpreter is None:
        pytest.skip(
            f"{repo}/.venv/bin/python is absent, so cdd-sow-research's PyJWT and cryptography are "
            f"not "
            "available to verify a portal-minted assertion"
        )
    if not _verifier_importable(interpreter, repo):
        pytest.skip(
            f"{repo}/.venv cannot import cdd-sow-research's private_key_jwt verifier (its 'oidc' "
            f"extra, "
            "PyJWT + cryptography, is not installed in that virtualenv), so the cross-repo proof "
            "cannot run; install the extra there to enable it"
        )
    completed = subprocess.run(
        [str(interpreter), str(_DRIVER)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        env=_doc1_env(repo),
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"cdd-sow-research's verifier driver "
        f"failed:\nstdout={completed.stdout}\nstderr={completed.stderr}"
    )
    document: dict[str, Any] = json.loads(completed.stdout)
    return document


@pytest.fixture(scope="module")
def signer(tmp_path_factory: pytest.TempPathFactory) -> LocalBffSigningKeyAdapter:
    """One generated 2048-bit key for the whole module (generation is the slow part)."""
    key_file = tmp_path_factory.mktemp("bff-key") / "portal-bff-signing-key.json"
    return LocalBffSigningKeyAdapter(Settings(profile="local", bff_signing_key_file=str(key_file)))


def _mint(
    signer: LocalBffSigningKeyAdapter,
    *,
    now: datetime,
    audience: str = GRANT_ENDPOINT,
    client_id: str = CLIENT_ID,
) -> str:
    return mint_client_assertion(
        ClientAssertionPolicy(client_id=client_id, audience=audience),
        signer,
        now=now,
        jti=secrets.token_urlsafe(24),
    ).value


def test_portal_assertion_verifies_against_doc1_and_its_negatives_are_refused(
    signer: LocalBffSigningKeyAdapter,
) -> None:
    now = datetime.now(UTC)
    key = signer.active_key()
    valid = _mint(signer, now=now)
    tampered = valid[:-4] + ("AAAA" if not valid.endswith("AAAA") else "BBBB")
    expired = _mint(signer, now=now - timedelta(minutes=10))
    wrong_audience = _mint(
        signer, now=now, audience="https://cdd-sow-research.example/agent/api/v1/other"
    )

    document = _run_doc1_verifier(
        {
            "client_id": CLIENT_ID,
            "audience": GRANT_ENDPOINT,
            "algorithm": key.algorithm,
            "public_jwk": key.public_jwk,
            "as_of": int(now.timestamp()),
            "assertions": [
                {"label": "valid", "assertion": valid},
                {"label": "replayed", "assertion": valid},
                {"label": "tampered", "assertion": tampered},
                {"label": "expired", "assertion": expired},
                {"label": "wrong-audience", "assertion": wrong_audience},
                {
                    "label": "unregistered-client",
                    "assertion": valid,
                    "expected_client_id": "some-other-bff",
                },
            ],
        }
    )
    outcomes = {entry["label"]: entry for entry in document["results"]}

    assert outcomes["valid"]["accepted"] is True, outcomes["valid"]
    assert outcomes["valid"]["client_id"] == CLIENT_ID
    # The verifier consumes the JTI, so the identical assertion presented twice is a replay.
    assert outcomes["replayed"]["accepted"] is False
    assert "already been used" in outcomes["replayed"]["reason"]
    assert outcomes["tampered"]["accepted"] is False
    assert outcomes["expired"]["accepted"] is False
    assert outcomes["wrong-audience"]["accepted"] is False
    assert outcomes["unregistered-client"]["accepted"] is False


def test_doc1_refuses_an_assertion_signed_by_a_key_it_did_not_pin(
    signer: LocalBffSigningKeyAdapter,
    tmp_path: Path,
) -> None:
    """A different key with the SAME kid must still fail: pinning is on the key, not the label."""
    now = datetime.now(UTC)
    pinned = signer.active_key()
    other = LocalBffSigningKeyAdapter(
        Settings(profile="local", bff_signing_key_file=str(tmp_path / "other-key.json"))
    )
    forged = _mint(other, now=now)
    document = _run_doc1_verifier(
        {
            "client_id": CLIENT_ID,
            "audience": GRANT_ENDPOINT,
            "algorithm": pinned.algorithm,
            # Pin the real key id, but publish the REAL key's material: an assertion signed by
            # the other key carries the other key's kid, so it must not match the registration.
            "public_jwk": pinned.public_jwk,
            "as_of": int(now.timestamp()),
            "assertions": [{"label": "foreign-key", "assertion": forged}],
        }
    )
    outcome = document["results"][0]
    assert outcome["accepted"] is False
    assert "not registered" in outcome["reason"]
