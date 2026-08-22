"""The BFF signing-key port: custody, the published JWK set, and the JOSE encodings under it."""

from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest

from journey_portal.adapters.local.bff_credentials import (
    LocalBffSigningKeyAdapter,
    accepted_keys,
)
from journey_portal.adapters.onprem.bff_credentials import OnPremBffSigningKeyAdapter
from journey_portal.config import Settings, build_container
from journey_portal.domain.jose import (
    b64u_decode,
    b64u_encode,
    b64u_uint,
    emsa_pkcs1_v15_sha256,
    jwk_thumbprint,
    parse_rsa_public_key_der,
    pem_to_der,
    rsa_public_jwk,
    uint_from_b64u,
)
from journey_portal.ports.bff_credentials import PublishedSigningKey, SigningKeyUnavailable

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def local_signer(tmp_path_factory: pytest.TempPathFactory) -> LocalBffSigningKeyAdapter:
    key_file = tmp_path_factory.mktemp("signing") / "portal-bff-signing-key.json"
    return LocalBffSigningKeyAdapter(Settings(profile="local", bff_signing_key_file=str(key_file)))


# --------------------------------------------------------------------------- custody
def test_local_key_is_generated_once_into_a_private_file(tmp_path: Path) -> None:
    key_file = tmp_path / "nested" / "portal-bff-signing-key.json"
    adapter = LocalBffSigningKeyAdapter(
        Settings(profile="local", bff_signing_key_file=str(key_file))
    )
    first = adapter.active_key()
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700

    # A second adapter over the same file reuses the key rather than minting a new identity:
    # a kid that changed on restart would silently invalidate a reviewed registration.
    reopened = LocalBffSigningKeyAdapter(
        Settings(profile="local", bff_signing_key_file=str(key_file))
    )
    assert reopened.active_key().kid == first.kid


def test_the_generated_key_is_a_2048_bit_rsa_key(local_signer: LocalBffSigningKeyAdapter) -> None:
    modulus = uint_from_b64u(local_signer.active_key().public_jwk["n"])
    assert modulus.bit_length() == 2048
    assert uint_from_b64u(local_signer.active_key().public_jwk["e"]) == 65537


def test_the_published_key_never_carries_private_material(
    local_signer: LocalBffSigningKeyAdapter,
    tmp_path: Path,
) -> None:
    published = local_signer.active_key().public_jwk
    assert set(published) == {"kty", "use", "alg", "kid", "n", "e"}
    # The port itself refuses a mis-shaped adapter, so a private member cannot reach the route
    # even if a future adapter built the JWK carelessly.
    with pytest.raises(ValueError, match="private key material"):
        PublishedSigningKey(
            kid="k1",
            algorithm="RS256",
            public_jwk={"kty": "RSA", "kid": "k1", "n": "AQAB", "e": "AQAB", "d": "AQAB"},
        )


def test_the_key_id_is_the_rfc_7638_thumbprint(local_signer: LocalBffSigningKeyAdapter) -> None:
    key = local_signer.active_key()
    assert key.kid == jwk_thumbprint(key.public_jwk)
    assert len(b64u_decode(key.kid)) == 32


def test_signing_refuses_a_key_id_that_is_not_the_active_one(
    local_signer: LocalBffSigningKeyAdapter,
) -> None:
    with pytest.raises(SigningKeyUnavailable):
        local_signer.sign(b"payload", kid="not-the-active-kid")


def test_a_damaged_key_file_refuses_rather_than_regenerating(tmp_path: Path) -> None:
    key_file = tmp_path / "portal-bff-signing-key.json"
    key_file.write_text(json.dumps({"kty": "RSA", "kid": "k1", "n": "AQAB"}))
    adapter = LocalBffSigningKeyAdapter(
        Settings(profile="local", bff_signing_key_file=str(key_file))
    )
    with pytest.raises(SigningKeyUnavailable, match="missing JWK members"):
        adapter.active_key()


def test_onprem_signing_fails_fast() -> None:
    adapter = OnPremBffSigningKeyAdapter(Settings(profile="onprem"))
    for call in (
        adapter.active_key,
        adapter.published_keys,
    ):
        with pytest.raises(NotImplementedError, match="portability placeholder"):
            call()
    with pytest.raises(NotImplementedError):
        adapter.sign(b"payload", kid="k1")


# --------------------------------------------------------------------------- rotation window
def test_published_keys_carry_the_reviewed_rotation_window(tmp_path: Path) -> None:
    previous = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "previous",
        "n": "zz",
        "e": "AQAB",
    }
    adapter = LocalBffSigningKeyAdapter(
        Settings(
            profile="local",
            bff_signing_key_file=str(tmp_path / "key.json"),
            bff_accepted_public_jwks=json.dumps([previous]),
        )
    )
    published = adapter.published_keys()
    assert [key.kid for key in published][1:] == ["previous"]
    assert published[0].kid == adapter.active_key().kid


def test_an_unset_rotation_window_publishes_only_the_active_key(
    local_signer: LocalBffSigningKeyAdapter,
) -> None:
    assert [key.kid for key in local_signer.published_keys()] == [local_signer.active_key().kid]


def test_a_malformed_rotation_window_refuses_rather_than_publishing_nothing() -> None:
    for raw in ("{not json", "{}", "[]", '["not-an-object"]'):
        with pytest.raises(SigningKeyUnavailable):
            accepted_keys(raw, active_kid="active")


# --------------------------------------------------------------------------- JOSE encodings
def test_base64url_round_trips_without_padding() -> None:
    for raw in (b"", b"\x00", b"abc", bytes(range(256))):
        encoded = b64u_encode(raw)
        assert "=" not in encoded
        assert b64u_decode(encoded) == raw


def test_jwk_integers_are_minimal_length_big_endian() -> None:
    assert b64u_uint(65537) == b64u_encode(b"\x01\x00\x01")
    assert uint_from_b64u(b64u_uint(0)) == 0
    with pytest.raises(ValueError):
        b64u_uint(-1)


def test_pkcs1_encoding_matches_rfc_8017() -> None:
    encoded = emsa_pkcs1_v15_sha256(b"payload", modulus_octets=256)
    octets = encoded.to_bytes(256, "big")
    assert octets[:2] == b"\x00\x01"
    # DigestInfo is 19 prefix octets plus a 32-octet digest, so the 0x00 separator sits at -52.
    assert octets[2:-52] == b"\xff" * (256 - 51 - 3)
    assert octets[-52] == 0x00
    assert octets[-51:-32].hex() == "3031300d060960864801650304020105000420"
    with pytest.raises(ValueError, match="too small"):
        emsa_pkcs1_v15_sha256(b"payload", modulus_octets=32)


def test_kms_public_key_der_is_parsed_back_to_the_same_jwk() -> None:
    """A hand-built SubjectPublicKeyInfo round-trips, so the KMS adapter needs no cryptography."""
    modulus = int.from_bytes(b"\x9f" + b"\x11" * 255, "big")
    exponent = 65537
    der = _rsa_spki(modulus, exponent)
    pem = "-----BEGIN PUBLIC KEY-----\n"
    body = base64.b64encode(der).decode("ascii")
    pem += "\n".join(body[index : index + 64] for index in range(0, len(body), 64))
    pem += "\n-----END PUBLIC KEY-----\n"
    assert parse_rsa_public_key_der(pem_to_der(pem)) == (modulus, exponent)
    assert rsa_public_jwk(modulus=modulus, exponent=exponent, kid="k1")["n"] == b64u_uint(modulus)


def test_a_truncated_der_document_is_refused() -> None:
    der = _rsa_spki(int.from_bytes(b"\x9f" + b"\x11" * 255, "big"), 65537)
    with pytest.raises(ValueError):
        parse_rsa_public_key_der(der[:-4])


# --------------------------------------------------------------------------- wiring
@pytest.mark.parametrize("profile", ["local", "gcp", "platform", "onprem"])
def test_the_signing_port_binds_in_every_profile(profile: str, tmp_path: Path) -> None:
    container = build_container(
        Settings(
            profile=profile,
            bff_signing_key_file=str(tmp_path / "key.json"),
            observability_url="http://localhost:8085",
            observability_audience="https://hrz5-audience.example.test",
        )
    )
    assert container.bff_signing_key is not None


def _rsa_spki(modulus: int, exponent: int) -> bytes:
    """Build a DER SubjectPublicKeyInfo for an RSA key, so the parser has a real document."""
    rsa_key = _der(0x30, _der(0x02, _uint(modulus)) + _der(0x02, _uint(exponent)))
    algorithm = _der(
        0x30,
        _der(0x06, bytes.fromhex("2a864886f70d010101")) + _der(0x05, b""),
    )
    return _der(0x30, algorithm + _der(0x03, b"\x00" + rsa_key))


def _uint(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return b"\x00" + raw if raw[0] & 0x80 else raw


def _der(tag: int, contents: bytes) -> bytes:
    length = len(contents)
    if length < 0x80:
        header = bytes((tag, length))
    else:
        encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
        header = bytes((tag, 0x80 | len(encoded))) + encoded
    return header + contents
