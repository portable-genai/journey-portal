"""On-prem BffSigningKeyPort: fail-fast portability placeholder.

An on-premises deployment holds the portal's service identity in its own HSM or key vault and
publishes the JWK set from its own infrastructure. This stub satisfies the port and refuses at
call time so the portability contract test proves the seam exists without pretending to sign,
and so a misconfigured migration cannot silently mint an identity from an unreviewed key.
"""

from __future__ import annotations

from ...config import Settings
from ...ports.bff_credentials import PublishedSigningKey

_MESSAGE = (
    "on-prem BFF signing is a portability placeholder: bind the client's own HSM or key vault "
    "and publish its JWK set (see docs/onprem-migration.md)"
)


class OnPremBffSigningKeyAdapter:
    """Satisfies BffSigningKeyPort but refuses at call time: the client wires its own custody."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def active_key(self) -> PublishedSigningKey:
        raise NotImplementedError(_MESSAGE)

    def sign(self, signing_input: bytes, *, kid: str) -> bytes:
        raise NotImplementedError(_MESSAGE)

    def published_keys(self) -> tuple[PublishedSigningKey, ...]:
        raise NotImplementedError(_MESSAGE)
