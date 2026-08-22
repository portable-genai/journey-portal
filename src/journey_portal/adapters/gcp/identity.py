"""GCP IdentityPort: verify the IAP-injected signed assertion (SDK imports stay lazy).

The portal runs behind Identity-Aware Proxy in the secure shapes, so this verifies the assertion
the edge injected and derives the portal-level :class:`Principal`. That principal's persona is what
the identity-injection policy then forwards to each embedded app (the same IAP assertion is passed
through unchanged, and every app re-verifies it itself: defense in depth).
"""

from __future__ import annotations

from hex_service_kit.assertion import require_claims, require_pinned_algorithm
from hex_service_kit.identity import IdentityError, Principal, RequestContext

from ...config import Settings
from ...ports.identity import VERIFIED

_IAP_ASSERTION_HEADER = "x-goog-iap-jwt-assertion"
_IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_IAP_ISSUER = "https://cloud.google.com/iap"

#: The claims this deployment requires before it reads any of them.
_REQUIRED_CLAIMS = ("iss", "sub", "email", "exp")


class IapIdentityAdapter:
    """Resolve a verified Principal from the Identity-Aware-Proxy assertion header."""

    #: The principal comes from the Identity-Aware-Proxy assertion, whose signature, issuer,
    #: expiry and audience are checked below; the caller cannot name itself. Read by the
    #: app-object exposure guard, which stands down for a profile binding this adapter (and
    #: for the platform subclass, which inherits this declaration with the verification).
    end_user_auth = VERIFIED

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve(self, ctx: RequestContext) -> Principal:  # pragma: no cover - needs live GCP
        assertion = ctx.header(_IAP_ASSERTION_HEADER)
        if not assertion:
            raise IdentityError("missing IAP assertion header")
        if not self._settings.iap_audience:
            raise IdentityError("PORTAL_IAP_AUDIENCE is required in the gcp profile")
        # Judged before the lazy import below, so the refusal costs no cloud SDK and the offline
        # gate can exercise it. `alg: none` is an unsigned assertion; HS* would make the public
        # key a signing secret.
        require_pinned_algorithm(assertion)
        # Lazy import: absent offline and in CI, so mypy treats google.* as untyped (see pyproject).
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token

        try:
            claims = id_token.verify_token(
                assertion,
                ga_requests.Request(),
                audience=self._settings.iap_audience,
                certs_url=_IAP_CERTS_URL,
            )
        except Exception as exc:  # noqa: BLE001 - translate the managed verifier boundary
            raise IdentityError("IAP assertion verification failed") from exc
        # The issuer and the claim SET are stated here: verify_token checks neither, and a
        # claim that is present but empty counts as missing.
        require_claims(
            claims,
            issuer=_IAP_ISSUER,
            audience=self._settings.iap_audience,
            required=_REQUIRED_CLAIMS,
        )
        email = str(claims["email"]).strip()
        return Principal(
            subject=email,
            tenant=str(claims.get("hd", "")),
            assurance="iap",
            source="gcp-iap",
        )
