"""The portal's access audit has a switch, default on, and the portal proves it before serving.

The fleet's runtime-control contract (2026-09-24): ``PORTAL_ACCESS_AUDIT`` is read in three
states; off binds a disabled audit whose integrity view says so and logs the posture at startup;
on, the health routes answer 503 until one content-free probe event has reached the sink and,
where the ledger is verifiable in process, the ledger verifies.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from journey_portal.adapters.controls import AUDIT_OFF_ACTION, DisabledAccessAudit
from journey_portal.api import app as app_module
from journey_portal.config import ACCESS_AUDIT_ENV, Container, Settings, build_container
from journey_portal.envread import ConfiguredEmptyError
from journey_portal.ports.access_audit import AuditUnavailable

from .conftest import LOOPBACK_PEER

_HEALTH_ROUTES = ("/healthz", "/v1/healthz")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv(ACCESS_AUDIT_ENV, raising=False)
    monkeypatch.setenv("PORTAL_PROFILE", "local")
    app_module._container.cache_clear()
    app_module.app.state.access_audit_proved = False
    yield
    app_module._container.cache_clear()
    app_module.app.state.access_audit_proved = False


# --------------------------------------------------------------------------- #
# Three states
# --------------------------------------------------------------------------- #
def test_the_audit_is_on_when_nothing_is_said() -> None:
    assert Settings.load().access_audit_enabled is True


def test_the_audit_switched_off_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ACCESS_AUDIT_ENV, "off")
    assert Settings.load().access_audit_enabled is False


def test_an_emptied_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ACCESS_AUDIT_ENV, "")
    with pytest.raises(ConfiguredEmptyError, match=ACCESS_AUDIT_ENV):
        Settings.load()


def test_an_unrecognised_switch_refuses_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ACCESS_AUDIT_ENV, "sometimes")
    with pytest.raises(ValueError, match=ACCESS_AUDIT_ENV):
        Settings.load()


# --------------------------------------------------------------------------- #
# Off: records nothing, says so
# --------------------------------------------------------------------------- #
def test_off_binds_the_disabled_audit_and_its_view_says_off() -> None:
    audit = Container(Settings(access_audit_enabled=False)).access_audit
    assert isinstance(audit, DisabledAccessAudit)
    view = audit.integrity()
    assert view.record_count == 0
    assert AUDIT_OFF_ACTION in view.suggested_actions


def test_on_binds_the_profile_audit() -> None:
    assert not isinstance(Container(Settings.load()).access_audit, DisabledAccessAudit)


def test_a_portal_with_the_audit_off_says_so_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="journey_portal.config"):
        build_container(Settings(access_audit_enabled=False))
    assert ACCESS_AUDIT_ENV in caplog.text


# --------------------------------------------------------------------------- #
# On: proved before the portal reports ready
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", _HEALTH_ROUTES)
def test_the_health_routes_answer_once_the_probe_reaches_the_ledger(path: str) -> None:
    with TestClient(app_module.app, client=LOOPBACK_PEER) as client:
        assert client.get(path).status_code == 200
    actions = [r.event.action for r in app_module._container().access_audit.records()]
    assert "startup-probe" in actions


@pytest.mark.parametrize("path", _HEALTH_ROUTES)
def test_a_portal_that_cannot_audit_does_not_report_ready(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    def _refuse(self: object, event: object) -> None:
        raise AuditUnavailable("sink unreachable")

    audit_cls = type(app_module._container().access_audit)
    monkeypatch.setattr(audit_cls, "append", _refuse)
    with TestClient(app_module.app, client=LOOPBACK_PEER) as client:
        response = client.get(path)
    assert response.status_code == 503
    assert "access audit" in response.json()["detail"]


def test_a_sink_that_recovers_is_picked_up_by_the_next_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_cls = type(app_module._container().access_audit)
    real_append = audit_cls.append
    calls = {"n": 0}

    def _refuse_once(self: object, event: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuditUnavailable("sink still starting")
        return real_append(self, event)  # type: ignore[arg-type]

    monkeypatch.setattr(audit_cls, "append", _refuse_once)
    with TestClient(app_module.app, client=LOOPBACK_PEER) as client:
        assert client.get("/v1/healthz").status_code == 503
        assert client.get("/v1/healthz").status_code == 200


def test_with_the_audit_off_the_portal_is_ready_without_a_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ACCESS_AUDIT_ENV, "false")
    with TestClient(app_module.app, client=LOOPBACK_PEER) as client:
        assert client.get("/v1/healthz").status_code == 200
    assert app_module._container().access_audit.records() == ()
