"""Port for the end-user subject token the cdd-sow-research grant exchanges (RFC 8693
subject_token).

cdd-sow-research's Mode 5 broker exchanges a token that identifies the END USER, verified against a
reviewed issuer profile, for a short-lived embedded-grant code. The portal must therefore hand the
broker a subject token minted by the deployment's identity provider for the user whose session it
just verified. That credential is issued by the IdP, not by the portal, so it is an external edge
and therefore a port.

The bound adapter decides where it comes from. The ``local`` family returns an obviously fictional
offline placeholder so the demo and the gate exercise the whole path without an IdP. The managed
families refuse, naming the exact deployment inputs still outstanding: cdd-sow-research's reviewed
ID-token profile requires a token from a DEDICATED Google OAuth client (issuer
``accounts.google.com``, ``aud`` and ``azp`` equal to that client, ``hd`` pinned), and the portal
cannot mint one until that client exists and a portal-side OIDC session holds its ID token. The
refusal is the honest state; see ``docs/named-deployment-dossier.md`` section 5.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class SubjectTokenUnavailable(RuntimeError):
    """No subject token can be produced for the verified principal, so no grant may be sought."""


@runtime_checkable
class SubjectTokenPort(Protocol):
    """Produce the end-user subject token for an already-verified portal principal."""

    def subject_token(self, *, subject: str, tenant: str) -> str:
        """Return the subject token, or raise ``SubjectTokenUnavailable``."""
        ...
