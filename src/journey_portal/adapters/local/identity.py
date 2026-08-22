"""Local IdentityPort: seeded dev personas (no IdP / AD / LDAP), from the commons.

The portal deliberately reuses the shared-commons ``DEFAULT_PERSONAS`` (``analyst`` / ``approver``
/ ``auditor`` / ``other-tenant``) rather than inventing its own: every embedded app seeds the same
set, so the persona id the portal injects as ``X-Dev-Persona`` always resolves inside each app.
The RM-vs-Ops split is the JOURNEY (which apps), not the persona (which role) - see
``docs/embedding-and-identity.md``.

The personas are an UNAUTHENTICATED grant of an identity the portal then INJECTS into every
embedded app, so this adapter refuses to construct unless the local profile was chosen
deliberately: the profile must actually be ``local`` and (when the settings came from the
environment) ``PORTAL_PROFILE`` must have been set rather than inherited from the fallback.
"""

from __future__ import annotations

from hex_service_kit.identity import (
    IdentityError,
    LocalPersonaIdentityAdapter,
    Principal,
    RequestContext,
)

from ...config import Settings
from ...ports.identity import CLIENT_ASSERTED


class LocalPersonaProfileError(IdentityError):
    """Raised when seeded dev personas would be served under a non-deliberate local profile."""


class LocalIdentityAdapter:
    """Resolve a verified Principal from a seeded dev persona (local profile only)."""

    #: The persona is chosen by the CALLER (picker header / cookie) and the portal then INJECTS
    #: the resulting principal into every embedded app, so this authenticates nobody. Read by
    #: the app-object exposure guard, which confines this profile to a loopback peer.
    end_user_auth = CLIENT_ASSERTED

    def __init__(self, settings: Settings) -> None:
        if settings.profile != "local":
            raise LocalPersonaProfileError(
                "seeded dev personas are local-profile only; "
                f"refusing to serve them under profile {settings.profile!r}"
            )
        if not settings.profile_explicit:
            raise LocalPersonaProfileError(
                "PORTAL_PROFILE is not set, so the local profile was inherited rather than "
                "chosen; the seeded dev personas are injected into every embedded app with no "
                "authentication and are refused. Set PORTAL_PROFILE=local deliberately for a "
                "dev or demo run, or PORTAL_PROFILE=gcp for a real deployment."
            )
        self._settings = settings
        self._inner = LocalPersonaIdentityAdapter()

    def resolve(self, ctx: RequestContext) -> Principal:
        return self._inner.resolve(ctx)

    def personas(self) -> tuple[dict[str, str], ...]:
        return self._inner.personas()
