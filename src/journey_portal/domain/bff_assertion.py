"""Mint the RFC 7523 ``private_key_jwt`` client assertion Doc1's Mode 5 broker verifies.

Pure: the clock and the JTI are arguments, so one call replays byte for byte and the whole
module is testable with no key, no network and no time source. The only impure edge is the
signature itself, taken through :class:`~journey_portal.ports.bff_credentials.BffSigningKeyPort`.

Every bound below is copied from what Doc1's ``PrivateKeyJwtVerifier`` actually enforces, read
before this module was written rather than after (``cdd-sow-research``,
``src/cdd_sow_research/adapters/oidc/private_key_jwt.py``):

* ``iss`` and ``sub`` both equal the registered BFF client id, and nothing else;
* ``aud`` is the EXACT grant endpoint, compared as a string, not an origin or a prefix;
* ``exp`` is after ``iat`` and the lifetime is at most the registered maximum (60 seconds),
  which is why the policy refuses to construct outside that band rather than failing at the
  verifier;
* ``jti`` matches ``^[A-Za-z0-9._~-]{22,256}$`` and is single-use, because the verifier consumes
  it in a replay store keyed on its SHA-256 digest. A repeated JTI is rejected there, so the
  caller must pass a fresh one per assertion;
* the protected header carries ``alg`` and ``kid`` and either omits ``typ`` or sets it to
  ``JWT``, and carries none of ``jku``, ``x5u``, ``jwk`` or ``x5c``, each of which the verifier
  treats as a token-controlled key reference and refuses outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..ports.bff_credentials import BffSigningKeyPort
from .jose import compact_jws, jws_signing_input

#: The verifier's own JTI shape. Enforced here so a bad value fails at the minter, where the
#: message is legible, rather than as an opaque 401 from the broker.
JTI_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{22,256}$")

#: Doc1's ``PrivateKeyJwtClientPolicy.max_lifetime_seconds`` upper bound.
MAX_LIFETIME_SECONDS = 60

CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


class AssertionPolicyError(ValueError):
    """The configured client assertion policy cannot produce an assertion Doc1 would accept."""


@dataclass(frozen=True, slots=True)
class ClientAssertionPolicy:
    """The reviewed registration this portal signs under."""

    client_id: str
    audience: str
    lifetime_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.client_id or len(self.client_id) > 256:
            raise AssertionPolicyError("BFF client id must be non-empty and at most 256 characters")
        if not self.audience or len(self.audience) > 512:
            raise AssertionPolicyError(
                "client assertion audience must be the exact grant endpoint, at most 512 characters"
            )
        if not 1 <= self.lifetime_seconds <= MAX_LIFETIME_SECONDS:
            raise AssertionPolicyError(
                f"client assertion lifetime must be from 1 to {MAX_LIFETIME_SECONDS} seconds"
            )


@dataclass(frozen=True, slots=True)
class ClientAssertion:
    """One minted assertion and the non-secret facts an audit record may carry."""

    value: str
    kid: str
    jti: str
    issued_at: int
    expires_at: int


def build_protected_header(*, kid: str, algorithm: str) -> dict[str, str]:
    """The protected header: exactly ``alg``, ``kid`` and ``typ``, and no key reference."""
    return {"alg": algorithm, "kid": kid, "typ": "JWT"}


def build_assertion_claims(
    policy: ClientAssertionPolicy, *, jti: str, issued_at: int
) -> dict[str, Any]:
    """The RFC 7523 claim set, with every member the verifier requires present."""
    if JTI_PATTERN.fullmatch(jti) is None:
        raise AssertionPolicyError(
            "client assertion jti must be 22 to 256 characters of [A-Za-z0-9._~-]"
        )
    return {
        "iss": policy.client_id,
        "sub": policy.client_id,
        "aud": policy.audience,
        "iat": issued_at,
        "exp": issued_at + policy.lifetime_seconds,
        "jti": jti,
    }


def mint_client_assertion(
    policy: ClientAssertionPolicy,
    signer: BffSigningKeyPort,
    *,
    now: datetime,
    jti: str,
) -> ClientAssertion:
    """Mint one single-use client assertion for ``now``, signed by the active key."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise AssertionPolicyError("client assertion clock must be timezone-aware")
    issued_at = int(now.astimezone(UTC).timestamp())
    key = signer.active_key()
    header = build_protected_header(kid=key.kid, algorithm=key.algorithm)
    claims = build_assertion_claims(policy, jti=jti, issued_at=issued_at)
    signing_input = jws_signing_input(header, claims)
    signature = signer.sign(signing_input, kid=key.kid)
    return ClientAssertion(
        value=compact_jws(signing_input, signature),
        kid=key.kid,
        jti=jti,
        issued_at=issued_at,
        expires_at=int(claims["exp"]),
    )
