#!/usr/bin/env python3
"""Unattended live-route demo check using the real BFF and deterministic fake upstreams."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi.testclient import TestClient

from journey_portal.api.app import _upstream, app
from journey_portal.config import Settings, load_journeys_mapping
from journey_portal.domain.models import UpstreamResponse

#: A loopback peer for the ``TestClient``. The app-object exposure guard refuses the
#: unauthenticated ``local`` posture to any other peer, and TestClient's DEFAULT peer is the
#: literal host ``"testclient"``, which is not a loopback address and is refused with a 503.
#: The suite pins the same peer in ``tests/conftest.py``, for the same reason.
LOOPBACK_PEER = ("127.0.0.1", 50000)


class EvidenceUpstream:
    """Record the exact request that crosses the portal trust boundary."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def forward(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        content: bytes,
    ) -> UpstreamResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "content": content}
        )
        return UpstreamResponse(
            status=200,
            headers=(("content-type", "application/json"),),
            body=b'{"status":"ok","profile":"local"}',
        )

    async def aclose(self) -> None:
        return None


def main() -> int:
    evidence = EvidenceUpstream()
    app.dependency_overrides[_upstream] = lambda: evidence
    try:
        with TestClient(app, client=LOOPBACK_PEER) as client:
            journeys = client.get("/v1/journeys")
            assert journeys.status_code == 200
            # Read the expectation from the SAME catalog the app reads, rather than freezing a
            # literal here. This assertion used to name two journeys; the config grew to five and
            # the demo went red on a change that had broken nothing. What the live route must
            # prove is that it serves the configured catalog, whatever the adopter configured.
            configured = load_journeys_mapping(Settings.load().journeys_path)["journeys"]
            assert isinstance(configured, dict) and configured, "the journey catalog is empty"
            assert {item["key"] for item in journeys.json()["journeys"]} == set(configured)
            assert journeys.headers["content-security-policy"] == "frame-ancestors 'self'"

            policy = client.get("/v1/embed-policy")
            assert policy.status_code == 200
            assert policy.json()["policy_id"] == "local-demo"
            assert policy.json()["decision"] == "allowed"

            selected = client.post("/v1/session/persona", json={"id": "approver"})
            assert selected.status_code == 200
            assert selected.json()["subject"] == "demo.approver@bank.example"

            proxied = client.post(
                "/agent/api/healthz",
                headers={
                    "X-Dev-Persona": "analyst",
                    "Authorization": "Bearer browser-forgery",
                },
                content=b"{}",
            )
            assert proxied.status_code == 200
            call = evidence.calls[-1]
            assert call["url"] == "http://127.0.0.1:8090/healthz"
            headers = call["headers"]
            assert isinstance(headers, dict)
            assert headers["x-dev-persona"] == "approver"
            assert "authorization" not in headers

            integrity = client.get("/v1/audit/integrity")
            assert integrity.status_code == 200
            assert integrity.json()["valid"] is True
            assert integrity.json()["record_count"] >= 1
    finally:
        app.dependency_overrides.clear()
    print(
        "PASS demo-selftest: live BFF routes, tenant policy, verified persona evidence "
        "and valid access ledger"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
