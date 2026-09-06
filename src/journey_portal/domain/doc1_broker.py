"""Build the host authorization proof and grant request cdd-sow-research's Mode 5 broker validates.

Pure: every check here is a deterministic function of the request's own provenance headers, the
reviewed policy and the ALREADY-VERIFIED principal. Nothing a browser can assert becomes part of
the proof; the browser only supplies the CSRF token it was issued, and that is verified before
:func:`build_host_proof` is ever reached.

The shape is copied from what cdd-sow-research enforces in
``EmbedBrokerService._validate_host_proof`` (``cdd-sow-research``,
``src/cdd_sow_research/api/embed.py``):

* ``host_origin`` must be one of the installation's exact parent origins, so the portal compares the
  browser's ``Origin`` against its own reviewed public origin before emitting it; * ``fetch_site``
  must be ``same-origin``, so the portal requires the Fetch Metadata header to say so rather than
  asserting it; * ``csrf_verified`` must be true, and the portal sets it only after actually
  verifying a token; * ``session_source_subject`` must equal the subject cdd-sow-research
  independently derives from the subject token, so the portal emits its VERIFIED principal's subject
  and nothing else; * ``session_binding`` must be a SHA-256 hex digest; * ``user_intent_id`` must
  match ``^[A-Za-z0-9._~:-]{16,256}$``.

The portal enforces the same rules on its own side first. That is deliberate duplication: a
proof that only the far side checks is a proof the near side can be tricked into signing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .bff_assertion import CLIENT_ASSERTION_TYPE

#: cdd-sow-research's own patterns, restated so a bad value fails here with a legible message.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_BINDING = re.compile(r"^[A-Za-z0-9._~:-]{16,256}$")

#: The RFC 8693 subject token type the reviewed Google installation accepts. cdd-sow-research
#: compares the
#: request against its INSTALLATION policy, so this must match what that installation configured.
ID_TOKEN_SUBJECT_TYPE = "urn:ietf:params:oauth:token-type:id_token"
ACCESS_TOKEN_SUBJECT_TYPE = "urn:ietf:params:oauth:token-type:access_token"
SUBJECT_TOKEN_TYPES = frozenset({ID_TOKEN_SUBJECT_TYPE, ACCESS_TOKEN_SUBJECT_TYPE})

#: The one Fetch Metadata value a grant may be initiated under.
REQUIRED_FETCH_SITE = "same-origin"


class BrokerPolicyError(ValueError):
    """The configured cdd-sow-research broker policy is incomplete, so no grant may be requested."""


class HostProofRejected(ValueError):
    """The request's own provenance fails the portal-side host authorization checks."""


@dataclass(frozen=True, slots=True)
class Doc1BrokerPolicy:
    """The reviewed, deployment-owned facts about the cdd-sow-research installation this portal
    fronts.
    """

    grant_endpoint: str
    installation_id: str
    bff_client_id: str
    portal_origin: str
    requested_scopes: tuple[str, ...]
    subject_token_type: str = ID_TOKEN_SUBJECT_TYPE

    def __post_init__(self) -> None:
        for name in ("grant_endpoint", "installation_id", "bff_client_id", "portal_origin"):
            if not str(getattr(self, name)).strip():
                raise BrokerPolicyError(f"cdd-sow-research broker policy {name} must be configured")
        if not self.grant_endpoint.startswith(("https://", "http://")):
            raise BrokerPolicyError("cdd-sow-research grant endpoint must be an absolute URL")
        if (
            self.portal_origin != self.portal_origin.rstrip("/")
            or "/" in self.portal_origin.split("//", 1)[-1]
        ):
            raise BrokerPolicyError(
                "portal origin must be a bare scheme and host with no path or trailing slash"
            )
        if not self.requested_scopes:
            raise BrokerPolicyError(
                "cdd-sow-research broker policy must request at least one scope"
            )
        if len(set(self.requested_scopes)) != len(self.requested_scopes) or any(
            not scope or len(scope) > 128 or any(character.isspace() for character in scope)
            for scope in self.requested_scopes
        ):
            raise BrokerPolicyError("requested scopes must be unique bounded tokens")
        if self.subject_token_type not in SUBJECT_TOKEN_TYPES:
            raise BrokerPolicyError("subject token type is not a reviewed RFC 8693 token type")


@dataclass(frozen=True, slots=True)
class HostAuthorizationProof:
    """The evidence cdd-sow-research requires that a real user, in a real
    session, asked for this.
    """

    host_origin: str
    fetch_site: str
    csrf_verified: bool
    session_binding: str
    session_source_subject: str
    user_intent_id: str

    def __post_init__(self) -> None:
        if self.fetch_site != REQUIRED_FETCH_SITE or self.csrf_verified is not True:
            raise HostProofRejected("host session controls were not verified")
        if SHA256_HEX.fullmatch(self.session_binding) is None:
            raise HostProofRejected("host session binding must be a SHA-256 correlation")
        if OPAQUE_BINDING.fullmatch(self.user_intent_id) is None:
            raise HostProofRejected("host user intent binding is invalid")
        if not self.session_source_subject or len(self.session_source_subject) > 512:
            raise HostProofRejected("host session subject must be a bounded verified subject")
        if not self.host_origin or len(self.host_origin) > 512:
            raise HostProofRejected("host origin must be a bounded exact origin")

    def as_payload(self) -> dict[str, Any]:
        return {
            "host_origin": self.host_origin,
            "fetch_site": self.fetch_site,
            "csrf_verified": self.csrf_verified,
            "session_binding": self.session_binding,
            "session_source_subject": self.session_source_subject,
            "user_intent_id": self.user_intent_id,
        }


def assess_browser_provenance(
    policy: Doc1BrokerPolicy,
    *,
    origin: str,
    fetch_site: str,
    fetch_mode: str = "",
    fetch_dest: str = "",
) -> None:
    """Refuse anything that is not a same-origin, script-initiated call from the portal itself.

    Checked BEFORE any credential is minted and before the broker is called, so a cross-site
    request never reaches cdd-sow-research and never consumes a JTI. ``Origin`` is compared exactly:
    a
    prefix or suffix comparison would accept ``https://portal.your-institution.example.attacker.example``.
    """
    if origin != policy.portal_origin:
        raise HostProofRejected("request Origin is not the portal's reviewed public origin")
    if fetch_site != REQUIRED_FETCH_SITE:
        raise HostProofRejected("request is not marked same-origin by Fetch Metadata")
    if fetch_mode and fetch_mode not in {"cors", "same-origin"}:
        raise HostProofRejected("request Fetch Metadata mode is not a script-initiated call")
    if fetch_dest and fetch_dest != "empty":
        raise HostProofRejected("request Fetch Metadata destination is not a script-initiated call")


def build_host_proof(
    policy: Doc1BrokerPolicy,
    *,
    subject: str,
    binding: str,
    user_intent_id: str,
) -> HostAuthorizationProof:
    """Assemble the proof from the VERIFIED principal, never from anything the client sent."""
    return HostAuthorizationProof(
        host_origin=policy.portal_origin,
        fetch_site=REQUIRED_FETCH_SITE,
        csrf_verified=True,
        session_binding=binding,
        session_source_subject=subject,
        user_intent_id=user_intent_id,
    )


def build_grant_request(
    policy: Doc1BrokerPolicy,
    *,
    instance_id: str,
    subject_token: str,
    client_assertion: str,
    proof: HostAuthorizationProof,
) -> dict[str, Any]:
    """The exact JSON body cdd-sow-research's ``POST /v1/embed/grants`` accepts."""
    if not 22 <= len(instance_id) <= 256:
        raise BrokerPolicyError("embed instance id must be 22 to 256 characters")
    if not subject_token or len(subject_token) > 16384:
        raise BrokerPolicyError("subject token must be present and at most 16384 characters")
    if not client_assertion or len(client_assertion) > 16384:
        raise BrokerPolicyError("client assertion must be present and at most 16384 characters")
    return {
        "installation_id": policy.installation_id,
        "instance_id": instance_id,
        "client_id": policy.bff_client_id,
        "client_assertion_type": CLIENT_ASSERTION_TYPE,
        "client_assertion": client_assertion,
        "subject_token_type": policy.subject_token_type,
        "subject_token": subject_token,
        "requested_scopes": list(policy.requested_scopes),
        "host_proof": proof.as_payload(),
    }
