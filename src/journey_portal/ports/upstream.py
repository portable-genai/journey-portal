"""The reverse-proxy edge of the hexagon: forward one HTTP call to an embedded app upstream.

The only outbound-HTTP port the portal has. The ``local`` / ``gcp`` adapters forward with an
async HTTP client; ``onprem`` is the fail-fast portability placeholder. Kept deliberately small
(one ``forward`` method) so the identity-injection and routing logic stays in the pure domain and
the adapter is just transport.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..domain.models import UpstreamResponse


@runtime_checkable
class UpstreamClientPort(Protocol):
    """Forward a single, already-sanitized HTTP request to an upstream and return its response."""

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
    ) -> UpstreamResponse:
        """Send ``method url`` with ``headers``/``content``; return the upstream response bytes."""
        ...

    async def aclose(self) -> None:
        """Release any pooled transport (idempotent)."""
        ...
