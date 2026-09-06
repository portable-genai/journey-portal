"""Port for the portal's own service-identity signing key (the cdd-sow-research Mode 5 BFF
credential).

The portal authenticates to cdd-sow-research's embedded-grant endpoint with ``private_key_jwt`` (RFC
7523), so it holds a signing key and publishes the matching public JWK set. Custody differs
completely by profile, which is exactly why this is a port: the ``local`` family keeps a generated
key in a gitignored file so the offline gate and the demo can sign, the managed families sign
through Cloud KMS where the private key is non-exportable and only a key VERSION is ever named, and
the on-premises family refuses so the seam is visible rather than pretended.

The port deliberately never returns private key material. A caller gets the public key it may
publish, and a signature over bytes it supplies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class SigningKeyUnavailable(RuntimeError):
    """No reviewed BFF signing key is configured, so the portal must not assert an identity."""


@dataclass(frozen=True, slots=True)
class PublishedSigningKey:
    """One public key the portal publishes, as it appears in the JWK set."""

    kid: str
    algorithm: str
    public_jwk: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.kid or len(self.kid) > 128:
            raise ValueError("BFF signing key id must be non-empty and bounded")
        if self.algorithm not in {"RS256", "ES256"}:
            raise ValueError("BFF signing algorithm must be RS256 or ES256")
        if not isinstance(self.public_jwk, dict) or not self.public_jwk:
            raise ValueError("BFF signing key must carry a public JWK")
        if self.public_jwk.get("kid") != self.kid:
            raise ValueError("published JWK kid must match the key id")
        if any(member in self.public_jwk for member in ("d", "p", "q", "dp", "dq", "qi", "k")):
            raise ValueError("published JWK must not carry private key material")


@runtime_checkable
class BffSigningKeyPort(Protocol):
    """Sign with the active BFF key and expose the public keys a verifier may pin."""

    def active_key(self) -> PublishedSigningKey:
        """The key new assertions are signed with; raise ``SigningKeyUnavailable`` if unset."""
        ...

    def sign(self, signing_input: bytes, *, kid: str) -> bytes:
        """Return the raw JWS signature over ``signing_input`` for the named key."""
        ...

    def published_keys(self) -> tuple[PublishedSigningKey, ...]:
        """The active key plus any still-accepted rotation-window keys, for the JWK set."""
        ...
