"""The Doc1 grant-initiating routes and the JWKS publication, at the HTTP boundary.

The load-bearing assertion in this module is negative: on a cross-site or CSRF-less request the
recording upstream must have recorded NOTHING. A route that refused with a 403 after already
calling the broker would look identical in a status-code-only test, while having consumed a JTI,
a rate-limit slot and a log line on somebody else's service.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from journey_portal.api import app as app_module
from journey_portal.api.doc1_grant import CSRF_PATH, GRANT_PATH
from journey_portal.domain.jose import jwk_thumbprint
from journey_portal.domain.models import UpstreamResponse
from tests.conftest import LOOPBACK_PEER

from .conftest import RecordingUpstream

# The BFF is exercised on a loopback origin, because the tenant embed policy admits only exact
# HTTPS origins plus loopback HTTP: a made-up test hostname could not be a reviewed origin.
ORIGIN = "http://localhost:8110"
TENANT_POLICIES = json.dumps(
    {
        "grant-fixture": {
            "tenant": "*",
            "hosts": ["localhost"],
            "frame_ancestors": ["'self'"],
            "cors_origins": [ORIGIN],
        }
    }
)
INSTANCE = "instance-fixture-0123456789"
BROKER_ANSWER = {
    "launch_code": "launch-code-fixture-01234",
    "state": "CODE_ISSUED",
    "expires_at": 0,
}


@pytest.fixture
def grant_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> Iterator[tuple[TestClient, RecordingUpstream]]:
    """A local-profile client whose Doc1 registration and signing key are test-owned."""
    monkeypatch.setenv("PORTAL_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", TENANT_POLICIES)
    monkeypatch.setenv("PORTAL_SESSION_SIGNING_KEY", "portal-session-signing-key-fixture")
    monkeypatch.setenv("PORTAL_BFF_SIGNING_KEY_FILE", str(tmp_path / "bff-signing-key.json"))
    monkeypatch.setenv("PORTAL_DOC1_GRANT_ENDPOINT", "https://doc1.example/v1/embed/grants")
    monkeypatch.setenv("PORTAL_DOC1_INSTALLATION_ID", "inst_fixture")
    monkeypatch.setenv("PORTAL_DOC1_BFF_CLIENT_ID", "hrz9-journey-portal-bff-fixture")
    recording_upstream.response = UpstreamResponse(
        status=200,
        headers=(("content-type", "application/json"),),
        body=json.dumps(BROKER_ANSWER).encode(),
        media_type="application/json",
    )
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    with TestClient(app_module.app, base_url=ORIGIN, client=LOOPBACK_PEER) as client:
        yield client, recording_upstream
    app_module.app.dependency_overrides.clear()
    app_module._container.cache_clear()


def _csrf(client: TestClient) -> str:
    response = client.get(CSRF_PATH)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["header"] == "X-CSRF-Token"
    assert (body["method"], body["path"]) == ("POST", GRANT_PATH)
    return str(body["csrf_token"])


def _same_origin_headers(token: str) -> dict[str, str]:
    return {
        "origin": ORIGIN,
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "X-CSRF-Token": token,
    }


# --------------------------------------------------------------------------- JWKS publication
def test_the_jwks_route_publishes_the_active_key_unauthenticated_and_cacheable(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, _ = grant_client
    response = client.get("/.well-known/doc1-bff-jwks.json")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "public, max-age=300"
    keys = response.json()["keys"]
    assert len(keys) == 1
    published = keys[0]
    assert published["kty"] == "RSA"
    assert published["alg"] == "RS256"
    assert published["use"] == "sig"
    assert published["kid"] == jwk_thumbprint(published)
    # The key id in the JWK set is the one an assertion's protected header carries.
    assert published["kid"] == app_module._container().bff_signing_key.active_key().kid


def test_the_jwks_route_never_emits_private_key_material(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, _ = grant_client
    published = client.get("/.well-known/doc1-bff-jwks.json").json()["keys"][0]
    assert set(published) == {"kty", "use", "alg", "kid", "n", "e"}


def test_the_jwks_route_needs_no_session_and_no_tenant_policy_decision(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    """It is fetched by a relying party that holds no credential of ours, from any host."""
    client, _ = grant_client
    response = client.get(
        "/.well-known/doc1-bff-jwks.json", headers={"host": "unregistered.example"}
    )
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors" in response.headers["content-security-policy"]


# --------------------------------------------------------------------------- refusals
def test_a_cross_site_request_never_reaches_the_broker(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    token = _csrf(client)
    headers = _same_origin_headers(token) | {
        "origin": "https://attacker.example",
        "sec-fetch-site": "cross-site",
    }
    response = client.post(GRANT_PATH, json={"instance_id": INSTANCE}, headers=headers)
    assert response.status_code == 403
    assert upstream.calls == []


def test_a_request_without_a_csrf_token_never_reaches_the_broker(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    headers = _same_origin_headers("")
    headers.pop("X-CSRF-Token")
    response = client.post(GRANT_PATH, json={"instance_id": INSTANCE}, headers=headers)
    assert response.status_code == 403
    assert upstream.calls == []


def test_a_csrf_token_minted_for_another_session_never_reaches_the_broker(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    token = _csrf(client)
    # Switch the seeded persona, so the verified principal (and therefore the binding) changes.
    assert client.post("/v1/session/persona", json={"id": "approver"}).status_code == 200
    response = client.post(
        GRANT_PATH, json={"instance_id": INSTANCE}, headers=_same_origin_headers(token)
    )
    assert response.status_code == 403
    assert upstream.calls == []


def test_a_same_origin_request_with_a_navigation_destination_is_refused(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    headers = _same_origin_headers(_csrf(client)) | {"sec-fetch-dest": "document"}
    assert (
        client.post(GRANT_PATH, json={"instance_id": INSTANCE}, headers=headers).status_code == 403
    )
    assert upstream.calls == []


def test_the_client_cannot_name_its_own_scopes_or_client_id(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    response = client.post(
        GRANT_PATH,
        json={"instance_id": INSTANCE, "requested_scopes": ["cdd.admin"]},
        headers=_same_origin_headers(_csrf(client)),
    )
    assert response.status_code == 422
    assert upstream.calls == []


# --------------------------------------------------------------------------- the authorized path
def test_an_authorized_request_sends_the_proof_and_the_assertion_to_the_broker(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    response = client.post(
        GRANT_PATH,
        json={"instance_id": INSTANCE},
        headers=_same_origin_headers(_csrf(client)),
    )
    assert response.status_code == 200, response.text
    assert response.json() == BROKER_ANSWER
    assert response.headers["cache-control"] == "private, no-store"

    assert len(upstream.calls) == 1
    call = upstream.calls[0]
    assert call["url"] == "https://doc1.example/v1/embed/grants"
    assert call["method"] == "POST"
    body = json.loads(call["content"])  # type: ignore[arg-type]
    assert body["installation_id"] == "inst_fixture"
    assert body["client_id"] == "hrz9-journey-portal-bff-fixture"
    assert body["instance_id"] == INSTANCE
    assert body["subject_token_type"] == "urn:ietf:params:oauth:token-type:id_token"
    assert body["client_assertion_type"] == (
        "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    )
    assert body["client_assertion"].count(".") == 2

    proof = body["host_proof"]
    assert proof["host_origin"] == ORIGIN
    assert proof["fetch_site"] == "same-origin"
    assert proof["csrf_verified"] is True
    # The subject is the PORTAL-VERIFIED principal, never anything the caller sent.
    assert proof["session_source_subject"] == client.get("/v1/whoami").json()["subject"]
    assert len(proof["session_binding"]) == 64
    assert 16 <= len(proof["user_intent_id"]) <= 256


def test_each_authorized_request_carries_a_fresh_jti_and_user_intent_id(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    """Doc1 consumes the JTI, so a reused one would make the second grant fail as a replay."""
    client, upstream = grant_client
    for _ in range(2):
        response = client.post(
            GRANT_PATH,
            json={"instance_id": INSTANCE},
            headers=_same_origin_headers(_csrf(client)),
        )
        assert response.status_code == 200, response.text
    bodies = [json.loads(call["content"]) for call in upstream.calls]  # type: ignore[arg-type]
    assertions = {body["client_assertion"] for body in bodies}
    intents = {body["host_proof"]["user_intent_id"] for body in bodies}
    assert len(assertions) == 2
    assert len(intents) == 2


def test_a_broker_refusal_is_relayed_as_a_bounded_gateway_error(
    grant_client: tuple[TestClient, RecordingUpstream],
) -> None:
    client, upstream = grant_client
    upstream.response = UpstreamResponse(
        status=401,
        headers=(("content-type", "application/json"),),
        body=b'{"detail": "embed authorization rejected"}',
        media_type="application/json",
    )
    response = client.post(
        GRANT_PATH,
        json={"instance_id": INSTANCE},
        headers=_same_origin_headers(_csrf(client)),
    )
    assert response.status_code == 502
    assert "embed authorization rejected" not in response.text


# --------------------------------------------------------------------------- unconfigured
def test_an_unconfigured_registration_refuses_and_names_the_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> None:
    monkeypatch.setenv("PORTAL_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", TENANT_POLICIES)
    monkeypatch.setenv("PORTAL_SESSION_SIGNING_KEY", "portal-session-signing-key-fixture")
    monkeypatch.setenv("PORTAL_BFF_SIGNING_KEY_FILE", str(tmp_path / "bff-signing-key.json"))
    # SET, and invalid: a relative grant endpoint is not an absolute URL, so the reviewed
    # policy cannot be constructed and the route must refuse instead of guessing one.
    monkeypatch.setenv("PORTAL_DOC1_GRANT_ENDPOINT", "doc1.example/v1/embed/grants")
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    try:
        with TestClient(app_module.app, base_url=ORIGIN, client=LOOPBACK_PEER) as client:
            token = _csrf(client)
            response = client.post(
                GRANT_PATH, json={"instance_id": INSTANCE}, headers=_same_origin_headers(token)
            )
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
        assert recording_upstream.calls == []
    finally:
        app_module.app.dependency_overrides.clear()
        app_module._container.cache_clear()
