"""Pure-stdlib JOSE primitives: base64url, compact JWS assembly, JWK and PKCS#1 encoding.

Nothing here holds key material or performs a modular exponentiation: it is the deterministic
encoding half of signing, so it lives in the domain where it can be unit-tested without a key,
without a clock and without an SDK. The half that touches a private key lives in an adapter
behind :mod:`journey_portal.ports.bff_credentials` (a local file-backed signer, a Cloud KMS
signer whose key is non-exportable, and the fail-fast on-premises placeholder).

The encodings are pinned to what cdd-sow-research's ``PrivateKeyJwtVerifier`` accepts and to what
``jwt.PyJWK`` can rebuild a public key from, so an assertion minted here verifies there:
unpadded base64url, compact JSON with sorted keys, a protected header carrying ``alg``/``kid``
and ``typ=JWT``, and RSASSA-PKCS1-v1_5 with SHA-256 (RFC 8017 EMSA-PKCS1-v1_5).
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any

#: DER ``DigestInfo`` prefix for SHA-256 (RFC 8017 section 9.2, note 1).
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")

#: The one signature algorithm this repo mints. cdd-sow-research registers RS256 or ES256; RS256 is
#: chosen
#: because Cloud KMS publishes an RSA public key this module can render as a JWK with no
#: cryptography dependency, keeping the local profile SDK-free.
RS256 = "RS256"


def b64u_encode(raw: bytes) -> str:
    """Base64url-encode without padding (RFC 7515 appendix C)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(value: str) -> bytes:
    """Decode unpadded base64url, restoring the padding the encoding drops."""
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def b64u_uint(value: int) -> str:
    """Encode a non-negative integer as a minimal-length big-endian base64url string."""
    if value < 0:
        raise ValueError("JWK integer members must be non-negative")
    length = max(1, (value.bit_length() + 7) // 8)
    return b64u_encode(value.to_bytes(length, "big"))


def uint_from_b64u(value: str) -> int:
    """Decode a base64url big-endian integer member of a JWK."""
    return int.from_bytes(b64u_decode(value), "big")


def compact_json(payload: Mapping[str, Any]) -> bytes:
    """Serialize deterministically: sorted keys, no whitespace, UTF-8."""
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")


def jws_signing_input(header: Mapping[str, Any], claims: Mapping[str, Any]) -> bytes:
    """The exact ASCII bytes a JWS signature covers: ``b64u(header).b64u(claims)``."""
    return b".".join(
        (
            b64u_encode(compact_json(header)).encode("ascii"),
            b64u_encode(compact_json(claims)).encode("ascii"),
        )
    )


def compact_jws(signing_input: bytes, signature: bytes) -> str:
    """Assemble the compact serialization from the covered bytes and the raw signature."""
    return f"{signing_input.decode('ascii')}.{b64u_encode(signature)}"


def rsa_public_jwk(
    *, modulus: int, exponent: int, kid: str, algorithm: str = RS256
) -> dict[str, str]:
    """Render an RSA public key as a signature-use JWK (RFC 7517)."""
    if modulus <= 0 or exponent <= 0:
        raise ValueError("RSA JWK members must be positive integers")
    if not kid or len(kid) > 128:
        raise ValueError("JWK kid must be non-empty and bounded")
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": algorithm,
        "kid": kid,
        "n": b64u_uint(modulus),
        "e": b64u_uint(exponent),
    }


def jwk_thumbprint(public_jwk: Mapping[str, Any]) -> str:
    """RFC 7638 SHA-256 thumbprint, the deterministic key id for a published RSA key."""
    if public_jwk.get("kty") != "RSA":
        raise ValueError("only RSA JWK thumbprints are supported")
    canonical = compact_json(
        {"e": public_jwk["e"], "kty": "RSA", "n": public_jwk["n"]},
    )
    return b64u_encode(hashlib.sha256(canonical).digest())


def emsa_pkcs1_v15_sha256(signing_input: bytes, *, modulus_octets: int) -> int:
    """Encode ``signing_input`` for RSASSA-PKCS1-v1_5 with SHA-256 and return it as an integer.

    ``EM = 0x00 || 0x01 || PS || 0x00 || DigestInfo`` where ``PS`` is at least eight ``0xFF``
    octets. A modulus too small to carry the padding is a configuration error, not a runtime
    one, so it raises rather than truncating.
    """
    digest_info = SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    if modulus_octets < len(digest_info) + 11:
        raise ValueError("RSA modulus is too small for a SHA-256 PKCS#1 v1.5 signature")
    padding = b"\xff" * (modulus_octets - len(digest_info) - 3)
    return int.from_bytes(b"\x00\x01" + padding + b"\x00" + digest_info, "big")


def parse_rsa_public_key_der(der: bytes) -> tuple[int, int]:
    """Extract ``(modulus, exponent)`` from a DER SubjectPublicKeyInfo carrying an RSA key.

    Cloud KMS publishes the verification key as a PEM SubjectPublicKeyInfo. Parsing the few DER
    nodes needed to reach the two integers keeps the managed adapter free of a cryptography
    dependency, so the SDK-free profiles still import it.
    """
    spki, rest = _der_read(der, expected_tag=0x30)
    if rest:
        raise ValueError("SubjectPublicKeyInfo has trailing bytes")
    _algorithm, after_algorithm = _der_read(spki, expected_tag=0x30)
    bit_string, after_bit_string = _der_read(after_algorithm, expected_tag=0x03)
    if after_bit_string:
        raise ValueError("SubjectPublicKeyInfo has unexpected trailing members")
    if not bit_string or bit_string[0] != 0:
        raise ValueError("RSA public key BIT STRING must have no unused bits")
    rsa_key, after_key = _der_read(bit_string[1:], expected_tag=0x30)
    if after_key:
        raise ValueError("RSAPublicKey has trailing bytes")
    modulus_bytes, after_modulus = _der_read(rsa_key, expected_tag=0x02)
    exponent_bytes, after_exponent = _der_read(after_modulus, expected_tag=0x02)
    if after_exponent:
        raise ValueError("RSAPublicKey has unexpected trailing members")
    modulus = int.from_bytes(modulus_bytes, "big")
    exponent = int.from_bytes(exponent_bytes, "big")
    if modulus <= 0 or exponent <= 0:
        raise ValueError("RSA public key members must be positive")
    return modulus, exponent


def pem_to_der(pem: str) -> bytes:
    """Strip the PEM armour and decode the base64 body."""
    lines = [line.strip() for line in pem.strip().splitlines()]
    body = [line for line in lines if line and not line.startswith("-----")]
    if not body:
        raise ValueError("PEM document contains no base64 body")
    return base64.b64decode("".join(body))


def _der_read(data: bytes, *, expected_tag: int) -> tuple[bytes, bytes]:
    """Read one DER TLV of ``expected_tag``; return its contents and the remaining bytes."""
    if len(data) < 2 or data[0] != expected_tag:
        raise ValueError(f"expected DER tag 0x{expected_tag:02x}")
    first_length = data[1]
    if first_length < 0x80:
        start, length = 2, first_length
    else:
        count = first_length & 0x7F
        if count == 0 or count > 4 or len(data) < 2 + count:
            raise ValueError("unsupported DER length encoding")
        start = 2 + count
        length = int.from_bytes(data[2:start], "big")
    end = start + length
    if end > len(data):
        raise ValueError("DER length runs past the end of the buffer")
    return data[start:end], data[end:]
