"""Managed SubjectTokenPort: refuses, naming exactly the deployment inputs still outstanding.

Doc1's reviewed Mode 5 installation accepts an OIDC ID token under the Google profile: issuer
``https://accounts.google.com``, ``aud`` and ``azp`` both equal to a DEDICATED Google OAuth client
used for nothing else, and ``hd`` pinned to the institution's Workspace domain. The portal cannot
produce one until two things exist, and neither can be invented here:

1. the dedicated Google OAuth client id (recorded as `PENDING` in Doc1's dossier section 5 and in
   this repo's ``docs/named-deployment-dossier.md``);
2. a portal-side OIDC login against that client, so the BFF holds the user's ID token
   server-side for the session it has already verified.

Until both land, this adapter refuses. That is the honest posture: a portal that guessed here
would send the broker a token from the wrong client, and the exchange would fail at Doc1 with a
message that pointed at the wrong side of the boundary. Refusing names the real gap instead.
"""

from __future__ import annotations

from ...config import Settings
from ...ports.subject_token import SubjectTokenUnavailable

PENDING_MESSAGE = (
    "the Doc1 Mode 5 subject token is not available: it requires the dedicated Google OAuth "
    "client id (PENDING in the named-deployment dossier, section 5) and a portal-side OIDC "
    "session holding that client's ID token. No grant may be requested until both exist."
)


class PendingGoogleSubjectTokenAdapter:
    """Satisfies SubjectTokenPort and refuses, so the missing input is visible, not guessed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def subject_token(self, *, subject: str, tenant: str) -> str:
        raise SubjectTokenUnavailable(PENDING_MESSAGE)
