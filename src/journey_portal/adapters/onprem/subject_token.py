"""On-prem SubjectTokenPort: fail-fast portability placeholder.

An on-premises deployment obtains the end-user subject token from the client's own identity
provider. This stub satisfies the port and refuses at call time so the seam is visible.
"""

from __future__ import annotations

from ...config import Settings


class OnPremSubjectTokenAdapter:
    """Satisfies SubjectTokenPort but refuses: the client wires its own IdP token source."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def subject_token(self, *, subject: str, tenant: str) -> str:
        raise NotImplementedError(
            "on-prem subject tokens are a portability placeholder: wire the client's own IdP "
            "token source for the brokered grant (see docs/onprem-migration.md)"
        )
