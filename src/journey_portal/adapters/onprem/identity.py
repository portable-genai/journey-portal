"""On-prem IdentityPort: fail-fast placeholder for the client's own IdP (OIDC / SAML)."""

from __future__ import annotations

from hex_service_kit.identity import Principal, RequestContext

from ...config import Settings
from ...ports.identity import UNIMPLEMENTED


class OnPremIdentityAdapter:
    """Satisfies IdentityPort but refuses at call time: the client wires its own IdP."""

    #: Resolves nobody until an adopter binds their own IdP verifier, so this deployment can
    #: authenticate no end user at all. Read by the app-object exposure guard, which confines
    #: the placeholder to loopback; rebinding this entry to a verifying adapter lifts that
    #: bound with no change to the guard.
    end_user_auth = UNIMPLEMENTED

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, ctx: RequestContext) -> Principal:
        raise NotImplementedError(
            "on-prem identity is a portability placeholder: bind the client's OIDC / SAML IdP "
            "adapter (see docs/onprem-migration.md)"
        )
