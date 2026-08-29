"""Stateless, session-bound CSRF tokens for the portal's grant-initiating route.

Modelled on Doc1's ``api/csrf.py``, which is the reference implementation in this programme, and
kept deliberately close to it so a reviewer comparing the two sees one design rather than two.
The differences are the two things the portal owns rather than Doc1: the binding is derived from
the portal's VERIFIED principal (there is no first-party session ``jti`` here), and the token is
bound to the exact unsafe method and path of the grant route rather than to a set of routes.

Properties that carry the security argument:

* the token is signed with a key derived from the deployment secret AND the session binding, so
  a token minted for one session cannot be replayed in another;
* it is bound to the exact method and path, so it cannot be replayed against a different action;
* it lives for 90 seconds, and the lifetime is asserted rather than merely encoded, so a token
  with a stretched ``exp`` fails the claim check;
* it is verified with a constant-time comparison, and every failure mode raises the same error;
* nothing is stored server-side, so there is no CSRF state to expire, replicate or leak.

Pure standard library: the clock and the nonce are arguments.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .jose import b64u_decode, b64u_encode, compact_json

CSRF_HEADER = "X-CSRF-Token"
TOKEN_TTL_SECONDS = 90
_TOKEN_VERSION = 1
_MIN_NONCE = 22
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_BINDING_LABEL = b"hrz9-cdd-sow-research-grant-csrf\x00"
_SESSION_LABEL = b"hrz9-portal-session-binding\x00"


class CsrfError(ValueError):
    """The supplied CSRF token is absent, malformed, expired or bound to something else."""


def session_binding(secret: bytes, *, subject: str, tenant: str) -> str:
    """Derive the hashed session binding the broker proof carries.

    A SHA-256 hex digest keyed on the deployment secret, so the value Doc1 records correlates a
    session without ever revealing the subject: Doc1's host-proof validation requires exactly
    ``^[0-9a-f]{64}$``, and a raw subject would put an identifier in another service's records.
    """
    if not secret:
        raise CsrfError("session binding requires a configured portal session signing key")
    if not subject or not tenant:
        raise CsrfError("session binding requires a verified subject and tenant")
    payload = f"{len(subject)}:{subject}|{len(tenant)}:{tenant}".encode()
    return hmac.new(secret, _SESSION_LABEL + payload, hashlib.sha256).hexdigest()


def mint_csrf_token(
    secret: bytes,
    *,
    binding: str,
    method: str,
    path: str,
    issued_at: int,
    nonce: str,
) -> str:
    """Mint one short-lived token bound to this session and this exact action."""
    canonical_method, canonical_path = _validate_target(method, path)
    if len(nonce) < _MIN_NONCE:
        raise CsrfError("CSRF nonce is too short to be unguessable")
    payload: dict[str, Any] = {
        "v": _TOKEN_VERSION,
        "nonce": nonce,
        "iat": issued_at,
        "exp": issued_at + TOKEN_TTL_SECONDS,
        "method": canonical_method,
        "path": canonical_path,
    }
    encoded = b64u_encode(compact_json(payload))
    return f"{encoded}.{b64u_encode(_sign(secret, binding, encoded))}"


def verify_csrf_token(
    token: str,
    secret: bytes,
    *,
    binding: str,
    method: str,
    path: str,
    now: int,
) -> None:
    """Verify signature, lifetime and the session/method/path binding, or raise ``CsrfError``."""
    canonical_method, canonical_path = _validate_target(method, path)
    if not token:
        raise CsrfError("CSRF token is required")
    try:
        encoded, supplied = token.split(".", 1)
        if not hmac.compare_digest(b64u_decode(supplied), _sign(secret, binding, encoded)):
            raise CsrfError("CSRF token signature does not match this session")
        payload: dict[str, Any] = json.loads(b64u_decode(encoded))
    except CsrfError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise CsrfError("CSRF token is invalid") from exc
    if (
        payload.get("v") != _TOKEN_VERSION
        or not isinstance(payload.get("nonce"), str)
        or len(payload["nonce"]) < _MIN_NONCE
        or not isinstance(payload.get("iat"), int)
        or isinstance(payload.get("iat"), bool)
        or not isinstance(payload.get("exp"), int)
        or isinstance(payload.get("exp"), bool)
        or payload["iat"] > now + 5
        or payload["exp"] < now
        or payload["exp"] - payload["iat"] != TOKEN_TTL_SECONDS
        or payload.get("method") != canonical_method
        or payload.get("path") != canonical_path
    ):
        raise CsrfError("CSRF token is invalid")


def _sign(secret: bytes, binding: str, encoded: str) -> bytes:
    return hmac.new(_binding_key(secret, binding), encoded.encode("ascii"), hashlib.sha256).digest()


def _binding_key(secret: bytes, binding: str) -> bytes:
    if not secret:
        raise CsrfError("CSRF protection requires a configured portal session signing key")
    if not binding:
        raise CsrfError("CSRF protection requires a current session binding")
    return hmac.new(secret, _BINDING_LABEL + binding.encode("ascii"), hashlib.sha256).digest()


def _validate_target(method: str, path: str) -> tuple[str, str]:
    canonical_method = method.strip().upper()
    if canonical_method not in UNSAFE_METHODS:
        raise CsrfError("CSRF tokens are issued only for unsafe HTTP methods")
    if (
        not path.startswith("/")
        or path.startswith("//")
        or any(character in path for character in ("?", "#", "\\"))
        or len(path) > 512
    ):
        raise CsrfError("CSRF target path is invalid")
    return canonical_method, path
