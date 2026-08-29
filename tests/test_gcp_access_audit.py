"""Managed access-audit delivery stays bounded and fails closed."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from journey_portal.adapters.gcp.access_audit import GcpAccessAuditAdapter
from journey_portal.config import Settings
from journey_portal.domain.models import PortalAccessEvent
from journey_portal.ports.access_audit import AuditUnavailable


def _adapter() -> GcpAccessAuditAdapter:
    return GcpAccessAuditAdapter(Settings(profile="gcp", audit_hmac_key="k" * 32))


def _event(adapter: GcpAccessAuditAdapter) -> PortalAccessEvent:
    return PortalAccessEvent(
        event_id="fictional-event",
        occurred_at="2026-07-29T12:00:00+00:00",
        actor_ref=adapter.reference("actor", "fictional.user@example.test"),
        tenant_ref=adapter.reference("tenant", "fictional-bank"),
        pseudonym_key_id=adapter.pseudonym_key_id,
        method="POST",
        action="forward:api",
        app_id="cdd-sow-research",
    )


def test_managed_append_writes_only_bounded_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()
    captured: list[dict[str, object]] = []

    def capture(payload: Mapping[str, object]) -> None:
        captured.append(dict(payload))

    monkeypatch.setattr(adapter, "_write", capture)
    adapter.append(_event(adapter))

    assert set(captured[0]) == {
        "action",
        "actor_ref",
        "app_id",
        "event_id",
        "method",
        "occurred_at",
        "pseudonym_key_id",
        "tenant_ref",
    }
    assert "fictional.user" not in str(captured)
    assert "fictional-bank" not in str(captured)


def test_managed_delivery_failure_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter()

    def fail(payload: Mapping[str, object]) -> None:
        raise OSError("fictional logging outage")

    monkeypatch.setattr(adapter, "_write", fail)
    with pytest.raises(AuditUnavailable, match="delivery failed"):
        adapter.append(_event(adapter))


def test_managed_profile_requires_pseudonym_key() -> None:
    with pytest.raises(AuditUnavailable, match="HMAC_KEY"):
        GcpAccessAuditAdapter(Settings(profile="gcp"))
