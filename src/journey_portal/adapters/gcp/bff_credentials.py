"""Managed BffSigningKeyPort: Cloud KMS asymmetric signing with a non-exportable private key.

The deployment names a KMS crypto key VERSION and nothing else: the private key never leaves the
HSM, never reaches this process and is never a value anybody can paste into a dossier. Signing is
one ``asymmetric_sign`` call over the SHA-256 digest of the JWS signing input, which is exactly
the RSASSA-PKCS1-v1_5 signature the portal's published JWK verifies.

The SDK import is lazy, so the SDK-free ``local`` and ``onprem`` gates import this module with no
cloud SDK installed. The published JWK is rendered from the PEM SubjectPublicKeyInfo KMS returns,
parsed with the domain's small DER reader rather than a cryptography dependency.
"""

from __future__ import annotations

from typing import Any

from ...config import Settings
from ...domain.jose import (
    RS256,
    jwk_thumbprint,
    parse_rsa_public_key_der,
    pem_to_der,
    rsa_public_jwk,
)
from ...ports.bff_credentials import PublishedSigningKey, SigningKeyUnavailable
from ..local.bff_credentials import accepted_keys

#: The one KMS algorithm this repo publishes a JWK for.
KMS_ALGORITHM = "RSA_SIGN_PKCS1_2048_SHA256"


class KmsBffSigningKeyAdapter:
    """Sign the client assertion with a regional Cloud KMS key version."""

    def __init__(self, settings: Settings) -> None:
        self._key_version = settings.bff_signing_key_version.strip()
        self._configured_kid = settings.bff_signing_kid.strip()
        self._accepted_raw = settings.bff_accepted_public_jwks
        self._cached: PublishedSigningKey | None = None

    def active_key(self) -> PublishedSigningKey:
        if self._cached is not None:
            return self._cached
        if not self._key_version:
            raise SigningKeyUnavailable(
                "PORTAL_BFF_SIGNING_KEY_VERSION is not set, so the portal holds no reviewed "
                "service identity and must not authenticate to the cdd-sow-research grant endpoint"
            )
        modulus, exponent = parse_rsa_public_key_der(pem_to_der(self._fetch_public_key_pem()))
        provisional = rsa_public_jwk(modulus=modulus, exponent=exponent, kid="pending")
        kid = self._configured_kid or jwk_thumbprint(provisional)
        public = rsa_public_jwk(modulus=modulus, exponent=exponent, kid=kid)
        self._cached = PublishedSigningKey(kid=kid, algorithm=RS256, public_jwk=public)
        return self._cached

    def sign(self, signing_input: bytes, *, kid: str) -> bytes:
        import hashlib

        if kid != self.active_key().kid:
            raise SigningKeyUnavailable("the requested key id is not the active KMS key version")
        digest = hashlib.sha256(signing_input).digest()
        response = self._client().asymmetric_sign(
            request={"name": self._key_version, "digest": {"sha256": digest}}
        )
        signature: bytes = response.signature
        if not signature:
            raise SigningKeyUnavailable("Cloud KMS returned an empty signature")
        return signature

    def published_keys(self) -> tuple[PublishedSigningKey, ...]:
        active = self.active_key()
        return (active, *accepted_keys(self._accepted_raw, active_kid=active.kid))

    # ------------------------------------------------------------------ managed
    def _client(self) -> Any:  # pragma: no cover - needs workload identity
        from google.cloud import kms

        return kms.KeyManagementServiceClient()

    def _fetch_public_key_pem(self) -> str:  # pragma: no cover - needs workload identity
        public_key = self._client().get_public_key(request={"name": self._key_version})
        algorithm = getattr(public_key.algorithm, "name", str(public_key.algorithm))
        if algorithm != KMS_ALGORITHM:
            raise SigningKeyUnavailable(
                f"the KMS key version signs with {algorithm}, but the published JWK profile "
                f"requires {KMS_ALGORITHM}"
            )
        return str(public_key.pem)
