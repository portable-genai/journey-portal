"""Platform transport uses authenticated HTTPS delegates and no business logic."""

from __future__ import annotations

from ..gcp.upstream import GcpUpstreamClient


class PlatformUpstreamClient(GcpUpstreamClient):
    """Thin authenticated delegate to the platform-hosted vertical services."""
