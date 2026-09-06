"""Local BffSigningKeyPort: an RS256 signer backed by a gitignored private JWK file.

SDK-free by construction. RSASSA-PKCS1-v1_5 signing is one modular exponentiation over an encoding
the domain already owns (:mod:`journey_portal.domain.jose`), so the offline profile signs a real,
verifiable assertion with nothing but the standard library. That is what lets the cross-repo fixture
verify a portal-minted assertion against cdd-sow-research's actual verifier on the offline gate.

Key custody follows the pattern the local access-audit adapter already established in this repo:
the key lives in a file under ``.local/`` (gitignored), created with 0600 permissions inside a
0700 directory, and generated on first use if absent. A private key is therefore NEVER committed
and never appears in a fixture. Generation uses ``secrets`` for candidate primes, so the key is
genuinely random rather than derived from a seed a reader could reproduce.

This adapter exists for dev, tests and the offline demo. A managed deployment binds the Cloud KMS
adapter instead, where the private key is non-exportable and only a key version is ever named.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from ...config import Settings
from ...domain.jose import (
    RS256,
    b64u_uint,
    emsa_pkcs1_v15_sha256,
    jwk_thumbprint,
    rsa_public_jwk,
    uint_from_b64u,
)
from ...ports.bff_credentials import PublishedSigningKey, SigningKeyUnavailable

_MODULUS_BITS = 2048
_PUBLIC_EXPONENT = 65537
_MILLER_RABIN_ROUNDS = 64
_SMALL_PRIMES = (
    3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97,
)  # fmt: skip


class LocalBffSigningKeyAdapter:
    """Sign with a locally held RSA key, generating one on first use if none exists."""

    def __init__(self, settings: Settings) -> None:
        self._path = Path(settings.bff_signing_key_file)
        self._accepted_raw = settings.bff_accepted_public_jwks
        self._private_jwk: dict[str, Any] | None = None

    # ------------------------------------------------------------------ port
    def active_key(self) -> PublishedSigningKey:
        jwk = self._load_or_create()
        public = rsa_public_jwk(
            modulus=uint_from_b64u(str(jwk["n"])),
            exponent=uint_from_b64u(str(jwk["e"])),
            kid=str(jwk["kid"]),
        )
        return PublishedSigningKey(kid=str(jwk["kid"]), algorithm=RS256, public_jwk=public)

    def sign(self, signing_input: bytes, *, kid: str) -> bytes:
        jwk = self._load_or_create()
        if kid != jwk["kid"]:
            raise SigningKeyUnavailable(
                "the requested key id is not the active local BFF signing key"
            )
        modulus = uint_from_b64u(str(jwk["n"]))
        private_exponent = uint_from_b64u(str(jwk["d"]))
        octets = (modulus.bit_length() + 7) // 8
        encoded = emsa_pkcs1_v15_sha256(signing_input, modulus_octets=octets)
        if encoded >= modulus:
            raise SigningKeyUnavailable("encoded message is not smaller than the RSA modulus")
        return pow(encoded, private_exponent, modulus).to_bytes(octets, "big")

    def published_keys(self) -> tuple[PublishedSigningKey, ...]:
        keys = [self.active_key()]
        keys.extend(accepted_keys(self._accepted_raw, active_kid=keys[0].kid))
        return tuple(keys)

    # ----------------------------------------------------------------- custody
    def _load_or_create(self) -> dict[str, Any]:
        if self._private_jwk is not None:
            return self._private_jwk
        if self._path.exists():
            self._private_jwk = self._read()
            return self._private_jwk
        self._private_jwk = self._create()
        return self._private_jwk

    def _read(self) -> dict[str, Any]:
        try:
            document = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SigningKeyUnavailable(
                f"local BFF signing key at {self._path} could not be read as a JWK"
            ) from exc
        if not isinstance(document, dict) or document.get("kty") != "RSA":
            raise SigningKeyUnavailable("local BFF signing key must be an RSA private JWK")
        missing = {"n", "e", "d", "kid"} - set(document)
        if missing:
            raise SigningKeyUnavailable(
                f"local BFF signing key is missing JWK members: {sorted(missing)}"
            )
        return dict(document)

    def _create(self) -> dict[str, Any]:
        modulus, private_exponent = _generate_rsa_key()
        public = rsa_public_jwk(modulus=modulus, exponent=_PUBLIC_EXPONENT, kid="pending")
        kid = jwk_thumbprint(public)
        document = {
            "kty": "RSA",
            "use": "sig",
            "alg": RS256,
            "kid": kid,
            "n": b64u_uint(modulus),
            "e": b64u_uint(_PUBLIC_EXPONENT),
            "d": b64u_uint(private_exponent),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, stat.S_IRWXU)
        descriptor = os.open(
            self._path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        with os.fdopen(descriptor, "w") as handle:
            json.dump(document, handle, sort_keys=True)
        return document


def accepted_keys(raw: str, *, active_kid: str) -> tuple[PublishedSigningKey, ...]:
    """Parse the reviewed rotation-window public keys, refusing anything but a JWK array.

    The accepted-key window is deployment-reviewed configuration (the dossier's "accepted
    verification keys" row): during a rotation the previous public key stays published so
    in-flight assertions still verify. An unset variable publishes only the active key; a SET
    but malformed one refuses rather than silently publishing nothing.
    """
    if not raw.strip():
        return ()
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SigningKeyUnavailable(
            "the accepted BFF verification keys must be a JSON array of public JWKs"
        ) from exc
    if not isinstance(document, list) or not document:
        raise SigningKeyUnavailable(
            "the accepted BFF verification keys must be a non-empty JSON array"
        )
    keys: list[PublishedSigningKey] = []
    for entry in document:
        if not isinstance(entry, dict):
            raise SigningKeyUnavailable("each accepted BFF verification key must be a JWK object")
        kid = str(entry.get("kid", ""))
        if kid == active_kid:
            continue
        keys.append(
            PublishedSigningKey(
                kid=kid,
                algorithm=str(entry.get("alg", RS256)),
                public_jwk=dict(entry),
            )
        )
    return tuple(keys)


def _generate_rsa_key() -> tuple[int, int]:
    """Generate a 2048-bit RSA key and return ``(modulus, private_exponent)``."""
    half = _MODULUS_BITS // 2
    while True:
        p = _generate_prime(half)
        q = _generate_prime(half)
        if p == q:
            continue
        modulus = p * q
        if modulus.bit_length() != _MODULUS_BITS:
            continue
        totient = (p - 1) * (q - 1)
        if totient % _PUBLIC_EXPONENT == 0:
            continue
        return modulus, pow(_PUBLIC_EXPONENT, -1, totient)


def _generate_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def _is_probable_prime(candidate: int) -> bool:
    """Miller-Rabin with random bases, after trial division by the small primes."""
    if candidate < 3 or candidate % 2 == 0:
        return False
    for prime in _SMALL_PRIMES:
        if candidate % prime == 0:
            return candidate == prime
    remainder = candidate - 1
    exponent_of_two = 0
    while remainder % 2 == 0:
        remainder //= 2
        exponent_of_two += 1
    for _ in range(_MILLER_RABIN_ROUNDS):
        base = secrets.randbelow(candidate - 3) + 2
        witness = pow(base, remainder, candidate)
        if witness in (1, candidate - 1):
            continue
        for _ in range(exponent_of_two - 1):
            witness = witness * witness % candidate
            if witness == candidate - 1:
                break
        else:
            return False
    return True
