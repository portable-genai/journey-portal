#!/usr/bin/env python3
"""Unattended live-route demo check using the real BFF and deterministic fake upstreams."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi.testclient import TestClient

from journey_portal.api.app import _upstream, app
from journey_portal.domain.models import UpstreamResponse


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
        with TestClient(app) as client:
            journeys = client.get("/v1/journeys")
            assert journeys.status_code == 200
            assert {item["key"] for item in journeys.json()["journeys"]} == {"rm", "ops"}
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
