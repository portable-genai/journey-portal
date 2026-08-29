"""The BFF end to end (offline): journeys feed, persona selection, identity-injecting proxy."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from journey_portal.api import app as app_module
from tests.conftest import LOOPBACK_PEER, RecordingUpstream


def test_healthz(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["profile"] == "local"
    assert body["region"] == "asia-southeast1"


def test_the_versioned_readiness_path_is_answered_by_the_application(client: TestClient) -> None:
    """The path a PROXIED probe may honestly use.

    On the deployment the serverless frontend answers ``/healthz`` itself, so that path never
    reaches this container and a probe against it reports healthy whether or not the application
    is running. ``/v1`` is not reserved by the platform, so this one is answered here. Found by
    running the managed trust-boundary suite against the named deployment on 2026-08-26, which
    reported the versioned path as a 404.
    """

    response = client.get("/v1/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["profile"] == "local"
    assert body["region"] == "asia-southeast1"
    assert body == client.get("/healthz").json(), (
        "the versioned readiness path must report exactly what /healthz reports, or the two "
        "probes disagree about the same container"
    )


def test_the_versioned_readiness_path_needs_no_tenant_session(client: TestClient) -> None:
    """It carries no tenant data, exactly like /healthz, and a probe holds no session."""

    from journey_portal.api.tenant_security import UNAUTHENTICATED_PATHS

    assert "/v1/healthz" in UNAUTHENTICATED_PATHS


def test_journeys_feed(client: TestClient) -> None:
    journeys = {j["key"]: j for j in client.get("/v1/journeys").json()["journeys"]}
    assert set(journeys) == {"rm", "ops", "mkt", "gov", "svc"}
    rm_apps = [a["id"] for a in journeys["rm"]["apps"]]
    ops_apps = [a["id"] for a in journeys["ops"]["apps"]]
    assert rm_apps == ["cdd-sow-research", "loan-document-intelligence", "cio-advisory"]
    assert ops_apps == [
        "credit-memo-drafting",
        "trade-finance-checker",
        "compliance-advisory",
        "human-review-console",
    ]
    # Each persona workbench is one ordered journey through its own systems, and the
    # review console is shared by the three that end in a human decision.
    assert [a["id"] for a in journeys["mkt"]["apps"]] == [
        "market-intelligence",
        "campaign-planner",
        "creative-studio",
        "marketing-compliance-gate",
        "performance-marketing-optimisation",
        "next-best-action",
        "human-review-console",
    ]
    assert [a["id"] for a in journeys["gov"]["apps"]] == [
        "architecture-validator",
        "model-quality-gate",
        "human-review-console",
    ]
    assert [a["id"] for a in journeys["svc"]["apps"]] == [
        "complaints-review",
        "compliance-advisory",
        "human-review-console",
    ]
    # An app appearing in two journeys is mounted once, so its route cannot diverge.
    assert journeys["ops"]["apps"][3]["api_base"] == journeys["gov"]["apps"][2]["api_base"]
    # the shells embed the same-origin ui_base and call the same-origin api_base
    doc1 = journeys["rm"]["apps"][0]
    assert doc1["ui_base"] == "/apps/cdd-sow-research/"
    assert doc1["api_base"] == "/agent/api"


def test_personas_listed_in_local(client: TestClient) -> None:
    ids = {p["id"] for p in client.get("/v1/personas").json()}
    assert {"analyst", "approver", "auditor"} <= ids


def test_whoami_defaults_to_first_persona(client: TestClient) -> None:
    body = client.get("/v1/whoami").json()
    assert body["persona"] == "analyst"  # the first seeded persona is the default


def test_select_persona_then_whoami(client: TestClient) -> None:
    set_resp = client.post("/v1/session/persona", json={"id": "approver"})
    assert set_resp.status_code == 200
    assert set_resp.json()["persona"] == "approver"
    # the cookie the portal set now drives identity on the next call
    assert client.get("/v1/whoami").json()["persona"] == "approver"


def test_select_unknown_persona_rejected(client: TestClient) -> None:
    assert client.post("/v1/session/persona", json={"id": "ghost"}).status_code == 422


def test_proxy_injects_default_identity_and_strips_spoof(
    client: TestClient, recording_upstream: RecordingUpstream
) -> None:
    resp = client.post(
        "/apps/cdd-sow-research/api/v1/cdd",
        headers={"X-Dev-Persona": "approver"},  # a browser trying to escalate the embedded app
        content=b'{"q": 1}',
    )
    assert resp.status_code == 200
    call = recording_upstream.last
    # forwarded to the app's BACKEND with the /apps/cdd-sow-research/api prefix stripped
    assert call["url"] == "http://127.0.0.1:8090/v1/cdd"
    headers = call["headers"]
    assert isinstance(headers, dict)
    # the injected persona is the portal-resolved DEFAULT (analyst), not the spoofed approver
    assert headers["x-dev-persona"] == "analyst"
    assert call["content"] == b'{"q": 1}'

    audit = client.get("/v1/audit/integrity").json()
    assert audit["valid"] is True
    assert audit["record_count"] == 3
    assert audit["escalates"] is False
    assert len(audit["head_hash"]) == 64


def test_proxy_uses_selected_persona(
    client: TestClient, recording_upstream: RecordingUpstream
) -> None:
    client.post("/v1/session/persona", json={"id": "auditor"})
    client.post("/apps/cdd-sow-research/api/v1/cdd", content=b"{}")
    headers = recording_upstream.last["headers"]
    assert isinstance(headers, dict)
    assert headers["x-dev-persona"] == "auditor"


def test_proxy_ui_forwards_full_path(
    client: TestClient, recording_upstream: RecordingUpstream
) -> None:
    client.get("/apps/cio-advisory/_next/static/chunk.js")
    call = recording_upstream.last
    # the UI hop forwards the full path unchanged (the app is basePath-aware)
    assert call["url"] == "http://127.0.0.1:3103/apps/cio-advisory/_next/static/chunk.js"
    record = app_module._container().access_audit.records()[-1]
    assert record.event.actor_ref == app_module._container().access_audit.reference(
        "actor",
        "demo.analyst@bank.example",
    )
    assert record.event.tenant_ref == app_module._container().access_audit.reference(
        "tenant",
        "demo-bank",
    )


def test_doc1_compatibility_entry_redirects_to_canonical_agent(client: TestClient) -> None:
    response = client.get("/apps/cdd-sow-research/?case=abc", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/agent/?case=abc"


def test_doc1_compatibility_asset_alias_redirects_to_canonical_agent(
    client: TestClient,
) -> None:
    response = client.get("/apps/cdd-sow-research/_next/static/chunk.js", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/agent/_next/static/chunk.js"


def test_doc1_canonical_ui_root_forwards_unmodified_artifact_path(
    client: TestClient, recording_upstream: RecordingUpstream
) -> None:
    response = client.get("/agent/")
    assert response.status_code == 200
    call = recording_upstream.last
    assert call["url"] == "http://127.0.0.1:3101/agent"


def test_doc1_canonical_asset_forwards_unmodified_artifact_path(
    client: TestClient, recording_upstream: RecordingUpstream
) -> None:
    response = client.get("/agent/_next/static/chunk.js")
    assert response.status_code == 200
    assert recording_upstream.last["url"] == ("http://127.0.0.1:3101/agent/_next/static/chunk.js")


def test_doc1_canonical_api_strips_prefix_and_injects_identity(
    client: TestClient, recording_upstream: RecordingUpstream
) -> None:
    response = client.post(
        "/agent/api/v1/cdd?trace=yes",
        headers={"X-Dev-Persona": "approver"},
        content=b'{"q": 2}',
    )
    assert response.status_code == 200
    call = recording_upstream.last
    assert call["url"] == "http://127.0.0.1:8090/v1/cdd?trace=yes"
    headers = call["headers"]
    assert isinstance(headers, dict)
    assert headers["x-dev-persona"] == "analyst"
    assert call["content"] == b'{"q": 2}'


def test_proxy_unknown_app_is_404(client: TestClient) -> None:
    assert client.get("/apps/ghost/api/v1/x").status_code == 404


def test_security_headers_present(client: TestClient) -> None:
    headers = client.get("/v1/journeys").headers
    assert headers["content-security-policy"] == "frame-ancestors 'self'"
    assert headers["x-frame-options"] == "SAMEORIGIN"
    assert headers["x-content-type-options"] == "nosniff"


def test_integrity_request_is_itself_evidenced(client: TestClient) -> None:
    response = client.get("/v1/audit/integrity")
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["record_count"] == 1
    assert len(response.json()["head_hash"]) == 64
    assert app_module._container().access_audit.records()[0].event.action == (
        "embed-policy:allowed"
    )


def _tenant_policy_json() -> str:
    return json.dumps(
        {
            "fictional-bank-v1": {
                "tenant": "demo-bank",
                "hosts": ["tenant.test.example"],
                "frame_ancestors": ["'self'", "https://host.test.example"],
                "cors_origins": ["https://host.test.example"],
            }
        }
    )


def test_exact_tenant_policy_drives_headers_and_reviewer_view(
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> None:
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", _tenant_policy_json())
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    try:
        with TestClient(
            app_module.app,
            base_url="https://tenant.test.example",
            client=LOOPBACK_PEER,
        ) as tenant_client:
            response = tenant_client.get(
                "/v1/embed-policy",
                headers={"Origin": "https://host.test.example"},
            )
    finally:
        app_module.app.dependency_overrides.clear()
        app_module._container.cache_clear()

    assert response.status_code == 200
    assert response.json()["policy_id"] == "fictional-bank-v1"
    assert response.json()["decision"] == "allowed"
    assert response.headers["access-control-allow-origin"] == "https://host.test.example"
    assert response.headers["content-security-policy"] == (
        "frame-ancestors 'self' https://host.test.example"
    )
    assert "x-frame-options" not in response.headers


def test_disallowed_origin_is_denied_before_upstream_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> None:
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", _tenant_policy_json())
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    try:
        with TestClient(
            app_module.app,
            base_url="https://tenant.test.example",
            client=LOOPBACK_PEER,
        ) as tenant_client:
            response = tenant_client.post(
                "/apps/cdd-sow-research/api/v1/cdd",
                headers={"Origin": "https://attacker.test"},
                content=b"fictional",
            )
            records = app_module._container().access_audit.records()
    finally:
        app_module.app.dependency_overrides.clear()
        app_module._container.cache_clear()

    assert response.status_code == 403
    assert response.json() == {"detail": "tenant embedding policy denied the request"}
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert recording_upstream.calls == []
    assert records[-1].event.action == "embed-policy:denied"


def test_verified_tenant_must_match_request_host_policy(
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> None:
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", _tenant_policy_json())
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    try:
        with TestClient(
            app_module.app,
            base_url="https://tenant.test.example",
            client=LOOPBACK_PEER,
        ) as tenant_client:
            assert (
                tenant_client.post("/v1/session/persona", json={"id": "other-tenant"}).status_code
                == 200
            )
            response = tenant_client.get("/v1/journeys")
    finally:
        app_module.app.dependency_overrides.clear()
        app_module._container.cache_clear()

    assert response.status_code == 403
    payload = response.text
    assert response.json() == {"detail": "tenant embedding policy denied the request"}
    assert "fictional-bank-v1" not in payload
    assert "demo-bank" not in payload


def test_allowed_cors_preflight_is_exact_and_content_free(
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> None:
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", _tenant_policy_json())
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    try:
        with TestClient(
            app_module.app,
            base_url="https://tenant.test.example",
            client=LOOPBACK_PEER,
        ) as tenant_client:
            response = tenant_client.options(
                "/apps/cdd-sow-research/api/v1/cdd",
                headers={
                    "Origin": "https://host.test.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
    finally:
        app_module.app.dependency_overrides.clear()
        app_module._container.cache_clear()

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://host.test.example"
    assert response.headers["access-control-allow-headers"] == (
        "authorization, content-type, x-dev-persona"
    )
    assert recording_upstream.calls == []


def test_unreviewed_cors_preflight_header_is_denied_and_evidenced(
    monkeypatch: pytest.MonkeyPatch,
    recording_upstream: RecordingUpstream,
) -> None:
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", _tenant_policy_json())
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    try:
        with TestClient(
            app_module.app,
            base_url="https://tenant.test.example",
            client=LOOPBACK_PEER,
        ) as tenant_client:
            response = tenant_client.options(
                "/apps/cdd-sow-research/api/v1/cdd",
                headers={
                    "Origin": "https://host.test.example",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "x-unreviewed-header",
                },
            )
            records = app_module._container().access_audit.records()
    finally:
        app_module.app.dependency_overrides.clear()
        app_module._container.cache_clear()

    assert response.status_code == 403
    assert response.headers["access-control-allow-origin"] == "https://host.test.example"
    assert records[-1].event.action == "embed-policy:denied-preflight"
    assert recording_upstream.calls == []


def test_health_progresses_while_access_audit_is_stalled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = app_module._container().access_audit
    release = threading.Event()

    def stalled_append(event: object) -> None:
        release.wait(timeout=2)

    monkeypatch.setattr(adapter, "append", stalled_append)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            started_at = asyncio.get_running_loop().time()
            audited_request = asyncio.create_task(async_client.get("/v1/journeys"))
            await asyncio.sleep(0)
            health = await asyncio.wait_for(async_client.get("/healthz"), timeout=0.5)
            elapsed = asyncio.get_running_loop().time() - started_at
            release.set()
            response = await audited_request

        assert health.status_code == 200
        assert elapsed < 0.5
        assert response.status_code == 200

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_corrupt_local_audit_store_fails_closed_with_503(
    client: TestClient,
    recording_upstream: RecordingUpstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.get("/v1/audit/integrity").status_code == 200
    adapter = app_module._container().access_audit

    def unavailable_connection():
        raise sqlite3.OperationalError("fictional locked database")

    monkeypatch.setattr(adapter, "_connect", unavailable_connection)

    response = client.post("/apps/cdd-sow-research/api/v1/cdd", content=b"fictional")

    assert response.status_code == 503
    assert response.json()["detail"] == "portal access audit is unavailable"
    assert recording_upstream.calls == []
