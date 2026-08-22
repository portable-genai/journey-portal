"""What an identity adapter DECLARES about the end-user authentication it can provide.

The ``IdentityPort`` Protocol itself comes from :mod:`hex_service_kit.identity`, beside the
``Principal`` and ``RequestContext`` it maps between, so there is one definition for the whole
catalog. What lives here is the DECLARATION the exposure guard reads.

The guard on the app object has one question to answer before it can decide anything: are this
portal's routes serving an authenticated end user? Nothing else in the configuration answers it,
and three things that look like they do, do not:

* The PROFILE names an adapter family, not an authentication scheme. A deliberate ``local`` and
  an inherited one bind the same seeded personas, and a client's own IdP adapter can be bound
  under ``onprem`` without the profile string changing at all.
* The SERVICE-TO-SERVICE or BFF credential authenticates a SERVICE. It authenticates no end user.
* The TENANT-SECURITY middleware (``api/tenant_security.py``) checks the request HOST HEADER
  against the reviewed tenant embed policy. That is a value the CLIENT wrote. It bounds which
  tenant a request may be framed as, not WHERE the request came from, and it was proved
  bypassable: a peer on the LAN sending ``Host: 127.0.0.1:8110`` was answered 200 with the full
  seeded principal on ``/v1/whoami``. The host check stays; it is a different control from a
  peer-address bound and neither substitutes for the other.

The adapter bound to the identity port is the only thing that knows, so it says so here, and
the guard reads the answer from the binding rather than inferring it from something else.

Three answers, and the difference between the first two is the whole point:

* :data:`VERIFIED` - the adapter resolves a principal from something it VERIFIES server side (a
  signed assertion whose signature, issuer, expiry and audience it checks). A caller cannot name
  itself, so end-user routes ARE authenticated.
* :data:`CLIENT_ASSERTED` - the adapter resolves a principal from something the CLIENT wrote
  (the seeded persona picker), and the portal then INJECTS that principal into every embedded
  app. A caller chooses who it is, so end-user routes are NOT authenticated, however many other
  credentials the deployment has configured.
* :data:`UNIMPLEMENTED` - the adapter resolves nobody at all: a portability placeholder waiting
  for the client's own IdP. Nobody can be authenticated, so nothing is.

An adapter that declares NOTHING is read as :data:`CLIENT_ASSERTED`, never :data:`VERIFIED`.
Silence is not a claim to verify anything, and a guard that reads silence as "authenticated"
switches itself off for every adapter somebody forgot to annotate, which is the fail-open shape
these declarations exist to remove.
"""

from __future__ import annotations

#: The adapter verifies a server-side assertion; the client cannot assert who it is.
VERIFIED = "verified"
#: The adapter believes something the client wrote. Useful offline, not authentication.
CLIENT_ASSERTED = "client-asserted"
#: The adapter resolves nobody: a placeholder for an identity provider not yet bound.
UNIMPLEMENTED = "unimplemented"

#: Every declaration this service understands. Anything else is read as :data:`CLIENT_ASSERTED`.
END_USER_AUTH_KINDS: frozenset[str] = frozenset({VERIFIED, CLIENT_ASSERTED, UNIMPLEMENTED})

#: The class attribute an identity adapter sets to one of the values above. A CLASS attribute,
#: not an instance one, because the posture has to be readable WITHOUT constructing the adapter:
#: the seeded-persona adapter REFUSES to construct under an inherited profile, and a posture that
#: can only be computed by constructing something disappears exactly when it matters most.
END_USER_AUTH_ATTR = "end_user_auth"


def declared_end_user_auth(adapter: object) -> str:
    """What ``adapter`` (a class or an instance) declares, defaulting to :data:`CLIENT_ASSERTED`.

    The default is the fail-closed one: it withholds the "authenticated" verdict the exposure
    guard would relax on, and claims nothing about an adapter that never spoke. An unrecognised
    value lands in the same place, so a typo in a declaration cannot read as a verification
    claim.
    """
    declared = getattr(adapter, END_USER_AUTH_ATTR, None)
    if isinstance(declared, str) and declared in END_USER_AUTH_KINDS:
        return declared
    return CLIENT_ASSERTED


__all__ = [
    "CLIENT_ASSERTED",
    "END_USER_AUTH_ATTR",
    "END_USER_AUTH_KINDS",
    "UNIMPLEMENTED",
    "VERIFIED",
    "declared_end_user_auth",
]
