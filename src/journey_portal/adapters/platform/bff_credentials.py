"""Platform BffSigningKeyPort: the same non-exportable KMS custody as the managed profile.

The platform profile changes where the vertical services live, not where the portal's service
identity is kept, so it rides the managed adapter unchanged rather than introducing a second
custody story for the same key.
"""

from __future__ import annotations

from ..gcp.bff_credentials import KmsBffSigningKeyAdapter


class PlatformBffSigningKeyAdapter(KmsBffSigningKeyAdapter):
    """Sign with the platform deployment's Cloud KMS key version."""
