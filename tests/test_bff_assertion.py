"""The RFC 7523 client assertion: every bound Doc1's verifier enforces, asserted here first.

These are the near-side mirror of ``tests/test_cross_repo_doc1_private_key_jwt.py``. The
cross-repo fixture proves the two halves agree; these prove the minter refuses a bad assertion
BEFORE it becomes a 401 from somebody else's service, where the message would be opaque.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from journey_portal.adapters.local.bff_credentials import LocalBffSigningKeyAdapter
from journey_portal.config import Settings
from journey_portal.domain.bff_assertion import (
    CLIENT_ASSERTION_TYPE,
    MAX_LIFETIME_SECONDS,
    AssertionPolicyError,
    ClientAssertionPolicy,
    build_assertion_claims,
    build_protected_header,
    mint_client_assertion,
)
from journey_portal.domain.jose import b64u_decode

CLIENT_ID = "hrz9-journey-portal-bff-fixture"
GRANT_ENDPOINT = "https://doc1.example/agent/api/v1/embed/grants"
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
JTI = "A" * 22


@pytest.fixture(scope="module")
def signer(tmp_path_factory: pytest.TempPathFactory) -> LocalBffSigningKeyAdapter:
    key_file = tmp_path_factory.mktemp("assertion") / "key.json"
    return LocalBffSigningKeyAdapter(Settings(profile="local", bff_signing_key_file=str(key_file)))


def _policy(**overrides: object) -> ClientAssertionPolicy:
    fields: dict[str, object] = {"client_id": CLIENT_ID, "audience": GRANT_ENDPOINT}
    fields.update(overrides)
    return ClientAssertionPolicy(**fields)  # type: ignore[arg-type]


def test_claims_are_exactly_what_the_verifier_requires() -> None:
    claims = build_assertion_claims(_policy(), jti=JTI, issued_at=int(NOW.timestamp()))
    assert set(claims) == {"iss", "sub", "aud", "iat", "exp", "jti"}
    assert claims["iss"] == claims["sub"] == CLIENT_ID
    assert claims["aud"] == GRANT_ENDPOINT
    assert claims["exp"] - claims["iat"] == MAX_LIFETIME_SECONDS


def test_the_protected_header_carries_no_token_controlled_key_reference() -> None:
    header = build_protected_header(kid="k1", algorithm="RS256")
    assert header == {"alg": "RS256", "kid": "k1", "typ": "JWT"}
    assert not {"jku", "x5u", "jwk", "x5c"} & set(header)


@pytest.mark.parametrize(
    "overrides",
    [
        {"client_id": ""},
        {"client_id": "c" * 257},
        {"audience": ""},
        {"audience": "a" * 513},
        {"lifetime_seconds": 0},
        {"lifetime_seconds": MAX_LIFETIME_SECONDS + 1},
    ],
)
def test_a_policy_the_verifier_would_reject_cannot_be_constructed(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(AssertionPolicyError):
        _policy(**overrides)


@pytest.mark.parametrize("jti", ["", "short", "A" * 21, "A" * 257, "has space", "has/slash"])
def test_a_jti_outside_the_verifier_pattern_is_refused(jti: str) -> None:
    with pytest.raises(AssertionPolicyError, match="jti"):
        build_assertion_claims(_policy(), jti=jti, issued_at=int(NOW.timestamp()))


def test_a_naive_clock_is_refused(signer: LocalBffSigningKeyAdapter) -> None:
    with pytest.raises(AssertionPolicyError, match="timezone-aware"):
        mint_client_assertion(_policy(), signer, now=datetime(2026, 8, 8, 12, 0), jti=JTI)


def test_the_minted_assertion_decodes_to_the_expected_header_and_claims(
    signer: LocalBffSigningKeyAdapter,
) -> None:
    assertion = mint_client_assertion(_policy(), signer, now=NOW, jti=JTI)
    header_segment, claims_segment, signature_segment = assertion.value.split(".")
    header = json.loads(b64u_decode(header_segment))
    claims = json.loads(b64u_decode(claims_segment))
    assert header["kid"] == signer.active_key().kid == assertion.kid
    assert header["alg"] == "RS256"
    assert claims["jti"] == JTI
    assert claims["iat"] == int(NOW.timestamp()) == assertion.issued_at
    assert claims["exp"] == assertion.expires_at
    # 2048-bit RSA signatures are exactly 256 octets, always.
    assert len(b64u_decode(signature_segment)) == 256


def test_minting_is_deterministic_for_the_same_clock_and_jti(
    signer: LocalBffSigningKeyAdapter,
) -> None:
    """RSASSA-PKCS1-v1_5 is deterministic, so a replayed run reproduces the bytes exactly."""
    first = mint_client_assertion(_policy(), signer, now=NOW, jti=JTI)
    second = mint_client_assertion(_policy(), signer, now=NOW, jti=JTI)
    assert first.value == second.value
    later = mint_client_assertion(_policy(), signer, now=NOW + timedelta(seconds=1), jti=JTI)
    assert later.value != first.value


def test_the_client_assertion_type_is_the_one_doc1_accepts() -> None:
    assert CLIENT_ASSERTION_TYPE == "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def test_an_unavailable_signing_key_stops_the_mint(tmp_path: Path) -> None:
    from journey_portal.adapters.onprem.bff_credentials import OnPremBffSigningKeyAdapter

    with pytest.raises(NotImplementedError):
        mint_client_assertion(
            _policy(),
            OnPremBffSigningKeyAdapter(Settings(profile="onprem")),
            now=NOW,
            jti=JTI,
        )
