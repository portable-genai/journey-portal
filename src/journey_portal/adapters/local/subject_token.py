"""Local SubjectTokenPort: an obviously fictional offline placeholder, never a real credential.

The local profile has no identity provider by design (seeded personas, no IdP/AD/LDAP), so there
is no real end-user token to hand the broker. This adapter returns a deterministic, plainly
fictional string derived from the verified persona so the offline gate and the demo exercise the
whole grant path (provenance checks, CSRF verification, proof assembly, assertion minting, the
outbound call) without inventing something that looks like a genuine token.

It is not a credential and no verifier accepts it: the prefix says so, and it carries no
signature. A real Doc1 installation rejects it, which is the correct outcome for a demo token.
"""

from __future__ import annotations

import hashlib

from ...config import Settings
from ...domain.jose import b64u_encode
from ...ports.subject_token import SubjectTokenUnavailable

#: Deliberately not a JWT and deliberately self-describing, so it can never be mistaken for one.
FIXTURE_PREFIX = "local-fixture-not-a-real-subject-token"


class LocalSubjectTokenAdapter:
    """Return a deterministic fictional subject token for the seeded local persona."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def subject_token(self, *, subject: str, tenant: str) -> str:
        if not subject or not tenant:
            raise SubjectTokenUnavailable(
                "the local subject-token fixture needs a verified subject and tenant"
            )
        digest = hashlib.sha256(
            f"{len(subject)}:{subject}|{len(tenant)}:{tenant}".encode()
        ).digest()
        return f"{FIXTURE_PREFIX}.{b64u_encode(digest)}"
