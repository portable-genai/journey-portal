"""Platform identity delegates to the verified IAP assertion contract."""

from __future__ import annotations

from ..gcp.identity import IapIdentityAdapter


class PlatformIdentityAdapter(IapIdentityAdapter):
    """Identity adapter for a shared-platform deployment behind IAP."""
