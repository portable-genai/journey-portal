"""GCP IdentityPort: verify the IAP-injected signed assertion (SDK imports stay lazy).

The portal runs behind Identity-Aware Proxy in the secure shapes, so this verifies the assertion
the edge injected and derives the portal-level :class:`Principal`. That principal's persona is what
the identity-injection policy then forwards to each embedded app (the same IAP assertion is passed
through unchanged, and every app re-verifies it itself: defense in depth).
"""

from __future__ import annotations

from collections.abc import Mapping

from hex_service_kit.assertion import require_claims, require_pinned_algorithm
from hex_service_kit.federation import IAP_ASSERTION_HEADER, IAP_ISSUER, IAP_KEYS_URL
from hex_service_kit.identity import IdentityError, Principal, RequestContext

from ...config import Settings
from ...ports.identity import VERIFIED

# The three transport facts are REBOUND from the kit, not re-declared here. This module was the
# one place in the portal the transport adoption missed: ``domain/identity_injection.py`` has
# taken them from the commons since tier 3 landed, while these three literals stayed behind and
# nothing could notice, because a literal always agrees with itself. Two modules in one
# repository disagreeing about which header carries identity is the exact drift the kit exists
# to make impossible.
_IAP_ASSERTION_HEADER = IAP_ASSERTION_HEADER
_IAP_CERTS_URL = IAP_KEYS_URL
_IAP_ISSUER = IAP_ISSUER

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
            # Carry the verifier's own words. "verification failed" is true of a wrong audience,
            # an expired assertion, an unreachable key set and a clock skew alike, and an
            # operator cannot act on any of them from that string.
            raise IdentityError(
                f"IAP assertion verification failed ({type(exc).__name__}: {exc})"
            ) from exc
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
            tenant=self._tenant_for(claims, email),
            assurance="iap",
            source="gcp-iap",
        )

    def _tenant_for(self, claims: Mapping[str, object], email: str) -> str:
        """Map the VERIFIED identity domain onto the deployment's reviewed tenant id.

        The tenant used to be the ``hd`` claim itself, which silently assumed the institution's
        Workspace domain and its tenant LABEL are the same string. They are not: a deployment
        whose reviewed tenant is ``reference-bank`` is signed into by people whose hosted domain
        is something else, so the tenant/host check in the embed registry compared two values
        that could never be equal and denied every request. Machine callers make it worse: a
        service account carries no ``hd`` at all, so its tenant was the empty string.

        With no map configured this returns the domain, exactly as before. With one configured
        an UNMAPPED domain resolves to the empty string rather than to itself, so an identity
        this deployment has not reviewed cannot land on a tenant by looking like one.
        """

        hosted_domain = str(claims.get("hd", "")).strip().lower()
        # A service account presents no hosted domain; its email domain is the closest thing to
        # one it has, and naming that domain in the map is how a deployment admits a machine
        # caller deliberately.
        domain = hosted_domain or email.rpartition("@")[2].strip().lower()
        mapping = self._settings.tenant_by_domain
        if not mapping:
            return domain
        return mapping.get(domain, "")
