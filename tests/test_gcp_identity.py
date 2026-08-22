from __future__ import annotations

import base64
import json
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from hex_service_kit.identity import IdentityError, RequestContext

from journey_portal.adapters.gcp.identity import (
    _IAP_CERTS_URL,
    _IAP_ISSUER,
    IapIdentityAdapter,
)
from journey_portal.config import Settings


def _install_fake_google(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any] | Exception,
    captured: dict[str, Any],
) -> None:
    google = ModuleType("google")
    auth = ModuleType("google.auth")
    transport = ModuleType("google.auth.transport")
    oauth2 = ModuleType("google.oauth2")
    id_token = ModuleType("google.oauth2.id_token")

    class Request:
        pass

    def verify_token(
        assertion: str,
        request: object,
        *,
        audience: str,
        certs_url: str,
    ) -> dict[str, Any]:
        captured.update(
            assertion=assertion,
            request=request,
            audience=audience,
            certs_url=certs_url,
        )
        if isinstance(claims, Exception):
            raise claims
        return claims

    transport.requests = SimpleNamespace(Request=Request)  # type: ignore[attr-defined]
    id_token.verify_token = verify_token  # type: ignore[attr-defined]
    google.auth = auth  # type: ignore[attr-defined]
    google.oauth2 = oauth2  # type: ignore[attr-defined]
    auth.transport = transport  # type: ignore[attr-defined]
    oauth2.id_token = id_token  # type: ignore[attr-defined]
    for name, module in {
        "google": google,
        "google.auth": auth,
        "google.auth.transport": transport,
        "google.oauth2": oauth2,
        "google.oauth2.id_token": id_token,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def _adapter() -> IapIdentityAdapter:
    return IapIdentityAdapter(
        Settings(
            profile="gcp",
            region="asia-southeast1",
            iap_audience="/projects/123/global/backendServices/456",
        )
    )


def _signed_assertion(alg: str = "RS256") -> str:
    """A structurally real compact JWS, because the algorithm pin reads the JOSE header.

    This fixture was the literal `"signed-iap-jwt"`, which was fine while nothing looked at the
    token before the (stubbed) verifier did. `require_pinned_algorithm` looks, so a fixture that
    is not a JWS is refused before it reaches the stub. Making the fixture real is the correct
    repair: a test whose token could never exist proves nothing about a token that can.
    """
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": alg, "typ": "JWT"}).encode())
        .decode()
        .rstrip("=")
    )
    payload = base64.urlsafe_b64encode(b'{"sub":"1"}').decode().rstrip("=")
    return f"{header}.{payload}.c2ln"


#: The claim set a real IAP assertion carries once verified, so a refusal row varies exactly
#: one claim and fails for the reason it names.
_GOOD_CLAIMS: dict[str, Any] = {
    "iss": _IAP_ISSUER,
    "sub": "accounts.google.com:stable-subject",
    "email": "rm@bank.example",
    "hd": "bank.example",
    "exp": 1_900_000_000,
    "aud": "/projects/123/global/backendServices/456",
}


def _context() -> RequestContext:
    return RequestContext(headers={"x-goog-iap-jwt-assertion": _signed_assertion()})


def test_iap_verifier_uses_exact_iap_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    _install_fake_google(
        monkeypatch,
        {
            "iss": _IAP_ISSUER,
            "sub": "accounts.google.com:stable-subject",
            "email": "rm@bank.example",
            "hd": "bank.example",
            "exp": 1_900_000_000,
            "aud": "/projects/123/global/backendServices/456",
        },
        captured,
    )

    principal = _adapter().resolve(_context())

    assert principal.subject == "rm@bank.example"
    assert captured["audience"] == "/projects/123/global/backendServices/456"
    assert captured["certs_url"] == _IAP_CERTS_URL
    assert captured["assertion"] == _signed_assertion()


@pytest.mark.parametrize(
    "claims",
    [
        {**_GOOD_CLAIMS, "iss": "https://accounts.google.com"},
        {**_GOOD_CLAIMS, "sub": ""},
        {**_GOOD_CLAIMS, "email": ""},
    ],
)
def test_iap_verifier_rejects_wrong_issuer_or_missing_identity(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any],
) -> None:
    _install_fake_google(monkeypatch, claims, {})

    with pytest.raises(IdentityError):
        _adapter().resolve(_context())


def test_iap_verifier_translates_google_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_google(monkeypatch, ValueError("bad signature"), {})

    with pytest.raises(IdentityError, match="verification failed"):
        _adapter().resolve(_context())
