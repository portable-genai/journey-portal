"""Hrz9 to Hrz5 content-free access-evidence contract."""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx
import pytest

from journey_portal.adapters.platform.access_audit import PlatformAccessAuditAdapter
from journey_portal.config import Settings
from journey_portal.domain.models import PortalAccessEvent
from journey_portal.domain.observability_audit import to_observability_audit_event
from journey_portal.ports.access_audit import AuditUnavailable


def _event(*, action: str = "forward:api") -> PortalAccessEvent:
    return PortalAccessEvent(
        event_id="fictional-event",
        occurred_at="2026-07-29T12:00:00+00:00",
        actor_ref="actor:v1:pseudonym",
        tenant_ref="tenant:v1:pseudonym",
        pseudonym_key_id="key-v1",
        method="POST",
        action=action,
        app_id="doc1",
    )


def _adapter(url: str = "https://observability.example.test") -> PlatformAccessAuditAdapter:
    return PlatformAccessAuditAdapter(
        Settings(
            profile="platform",
            audit_hmac_key="k" * 32,
            observability_url=url,
            observability_audience="https://observability-audience.example.test",
        )
    )


def test_domain_mapping_is_content_free_deterministic_and_hrz5_compatible() -> None:
    event = _event()

    first = to_observability_audit_event(event)
    repeated = to_observability_audit_event(event)

    assert first == repeated
    assert first["actor"] == event.actor_ref
    assert first["decision"] == "allowed"
    assert first["redacted_prompt"] == ""
    assert first["redacted_response"] == ""
    assert first["resource"] == "hrz9-journey-portal/doc1"
    assert first["metadata"] == {
        "event_id": "fictional-event",
        "method": "POST",
        "pseudonym_key_id": "key-v1",
        "source": "hrz9-journey-portal",
        "tenant_ref": "tenant:v1:pseudonym",
    }


def test_denied_policy_event_maps_to_blocked_decision() -> None:
    denied = to_observability_audit_event(_event(action="embed-policy:denied"))
    assert denied["decision"] == "blocked"
    preflight = to_observability_audit_event(_event(action="embed-policy:denied-preflight"))
    assert preflight["decision"] == "blocked"


def test_policy_event_never_exposes_raw_tenant_or_policy_id() -> None:
    event = PortalAccessEvent(
        event_id="fictional-event",
        occurred_at="2026-07-29T12:00:00+00:00",
        actor_ref="actor:v1:pseudonym",
        tenant_ref="tenant:v1:pseudonym",
        pseudonym_key_id="key-v1",
        method="GET",
        action="embed-policy:denied",
        app_id="portal:fictional-bank-primary",
    )

    payload = to_observability_audit_event(event)
    serialized = json.dumps(payload, sort_keys=True)

    assert payload["resource"] == "hrz9-journey-portal/embed-policy"
    assert "fictional-bank" not in serialized
    assert "fictional-bank-primary" not in serialized


def test_platform_adapter_posts_exact_contract_with_workload_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        adapter,
        "_fetch_token",
        lambda audience: f"token-for:{audience}",
    )

    def capture(
        url: str,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> None:
        captured.update(url=url, payload=dict(payload), headers=dict(headers))

    monkeypatch.setattr(adapter, "_post", capture)
    adapter.append(_event())

    assert captured["url"] == "https://observability.example.test/v1/audit"
    assert captured["headers"] == {
        "authorization": "Bearer token-for:https://observability-audience.example.test"
    }
    assert captured["payload"] == to_observability_audit_event(_event())


def test_platform_adapter_allows_sdk_free_loopback_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter("http://localhost:8085")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        adapter,
        "_post",
        lambda url, payload, headers: captured.update(headers=dict(headers)),
    )

    adapter.append(_event())

    assert captured["headers"] == {}


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("observability_url", "https://observability.example.test/path"),
        ("observability_url", "https://observability.example.test:443"),
        ("observability_url", "https://HRZ5.example.test"),
        ("observability_audience", "https://observability-audience.example.test/path"),
        ("observability_audience", "http://localhost:8085"),
    ],
)
def test_platform_adapter_rejects_runtime_origin_drift(setting: str, value: str) -> None:
    values = {
        "profile": "platform",
        "audit_hmac_key": "k" * 32,
        "observability_url": "https://observability.example.test",
        "observability_audience": "https://observability-audience.example.test",
    }
    values[setting] = value

    with pytest.raises(ValueError, match="exact lowercase HTTPS origin"):
        PlatformAccessAuditAdapter(Settings(**values))  # type: ignore[arg-type]


def test_platform_adapter_fails_closed_on_hrz5_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_fetch_token", lambda audience: "fictional-token")
    monkeypatch.setattr(
        adapter,
        "_post",
        lambda url, payload, headers: (_ for _ in ()).throw(OSError("fictional outage")),
    )

    with pytest.raises(AuditUnavailable, match="delivery failed"):
        adapter.append(_event())


@pytest.mark.parametrize("status_code", [401, 500])
def test_platform_adapter_fails_closed_on_non_success_response(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    monkeypatch.setattr(adapter, "_fetch_token", lambda audience: "fictional-token")
    monkeypatch.setattr(
        "journey_portal.adapters.platform.access_audit.httpx.post",
        lambda *args, **kwargs: httpx.Response(status_code),
    )

    with pytest.raises(AuditUnavailable, match=f"HTTP {status_code}"):
        adapter.append(_event())


def test_platform_adapter_fails_closed_when_workload_token_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    monkeypatch.setattr(
        adapter,
        "_fetch_token",
        lambda audience: (_ for _ in ()).throw(OSError("fictional metadata outage")),
    )

    with pytest.raises(AuditUnavailable, match="delivery failed"):
        adapter.append(_event())
