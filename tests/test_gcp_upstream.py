"""Managed upstream authentication and target validation."""

from __future__ import annotations

import asyncio

import pytest

from journey_portal.adapters.gcp.upstream import GcpUpstreamClient
from journey_portal.config import Settings
from journey_portal.domain.models import UpstreamResponse


def test_gcp_transport_rejects_plaintext_target() -> None:
    with pytest.raises(ValueError, match="https"):
        GcpUpstreamClient._audience("http://service.internal/path")


def test_gcp_transport_uses_exact_origin_as_audience() -> None:
    assert (
        GcpUpstreamClient._audience("https://service.example.test/path?q=1")
        == "https://service.example.test"
    )


def test_gcp_transport_overwrites_browser_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_forward(
        self: object,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        content: bytes,
    ) -> UpstreamResponse:
        captured.update(method=method, url=url, headers=headers, content=content)
        return UpstreamResponse(200, (), b"ok")

    monkeypatch.setattr(
        GcpUpstreamClient,
        "_fetch_token",
        staticmethod(lambda audience: f"signed-for:{audience}"),
    )
    monkeypatch.setattr(
        "journey_portal.adapters.local.upstream.HttpxUpstreamClient.forward",
        fake_forward,
    )
    client = GcpUpstreamClient(Settings(profile="gcp"))
    response = asyncio.run(
        client.forward(
            method="GET",
            url="https://service.example.test/v1",
            headers={"authorization": "Bearer browser"},
            content=b"",
        )
    )
    assert response.status == 200
    assert captured["headers"] == {
        "authorization": "Bearer signed-for:https://service.example.test"
    }
