"""On-prem UpstreamClientPort: fail-fast portability placeholder.

An on-prem deployment wires its own network egress path to the embedded apps (service mesh,
internal LB). This stub satisfies the port and refuses at call time so the portability contract
test proves the seam exists without pretending to forward.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...config import Settings
from ...domain.models import UpstreamResponse


class OnPremUpstreamClient:
    """Satisfies UpstreamClientPort but refuses at call time: the client wires its own egress."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
    ) -> UpstreamResponse:
        raise NotImplementedError(
            "on-prem reverse proxy is a portability placeholder: wire the client's own egress "
            "path to the embedded app services (see docs/onprem-migration.md)"
        )

    async def aclose(self) -> None:
        return None
