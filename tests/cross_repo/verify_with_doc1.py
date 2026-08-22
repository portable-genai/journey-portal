"""Verify a portal-minted client assertion with Doc1's OWN verifier, in Doc1's OWN interpreter.

Run by ``tests/test_cross_repo_doc1_private_key_jwt.py`` as a subprocess under the sibling
``cdd-sow-research`` checkout's virtualenv, because that is where ``PyJWT`` and ``cryptography``
live. Nothing is copied or re-implemented: this imports
``cdd_sow_research.adapters.oidc.private_key_jwt`` from that repo's working tree, so the proof
tracks Doc1's real verifier rather than a snapshot of it.

Reads one JSON request on stdin and writes one JSON result on stdout. Entirely offline.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime


def main() -> int:
    request = json.load(sys.stdin)
    from cdd_sow_research.adapters.oidc.private_key_jwt import (
        InMemoryClientAssertionReplayStore,
        PinnedClientKey,
        PrivateKeyJwtClientPolicy,
        PrivateKeyJwtVerifier,
    )
    from cdd_sow_research.domain.identity import IdentityError

    policy = PrivateKeyJwtClientPolicy(
        client_id=request["client_id"],
        audience=request["audience"],
        keys=(
            PinnedClientKey(
                kid=request["public_jwk"]["kid"],
                algorithm=request["algorithm"],
                public_jwk=request["public_jwk"],
            ),
        ),
    )
    verifier = PrivateKeyJwtVerifier((policy,), InMemoryClientAssertionReplayStore())
    as_of = datetime.fromtimestamp(request["as_of"], tz=UTC)

    results = []
    for attempt in request["assertions"]:
        try:
            verified = verifier.verify(
                attempt["assertion"],
                expected_client_id=attempt.get("expected_client_id", policy.client_id),
                as_of=as_of,
            )
        except (IdentityError, ValueError) as exc:
            results.append({"label": attempt["label"], "accepted": False, "reason": str(exc)})
        else:
            results.append(
                {
                    "label": attempt["label"],
                    "accepted": True,
                    "client_id": verified.client_id,
                    "correlation": verified.assertion_correlation,
                    "expires_at": int(verified.expires_at.timestamp()),
                }
            )
    json.dump({"results": results}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
