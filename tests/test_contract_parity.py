"""Port/adapter parity: every port binds and conforms in every profile, importable offline.

The hexagon's load-bearing contract: switching the whole stack is a one-line profile change with
no domain edits, and the local / onprem profiles import every adapter with no cloud SDK installed.
"""

from __future__ import annotations

import pytest
from hex_service_kit.identity import IdentityPort

from journey_portal.config import Settings, build_container
from journey_portal.ports import (
    AccessAuditPort,
    BffSigningKeyPort,
    SubjectTokenPort,
    UpstreamClientPort,
)


@pytest.mark.parametrize("profile", ["local", "gcp", "platform", "onprem"])
def test_every_port_binds_and_conforms(profile: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    container = build_container(
        Settings(
            profile=profile,
            local_audit_db=str(tmp_path / "audit.sqlite3"),
            audit_hmac_key="k" * 32,
            observability_url="http://localhost:8085",
            observability_audience="https://observability-audience.example.test",
            bff_signing_key_file=str(tmp_path / "bff-signing-key.json"),
        )
    )
    # Construction imports the adapter module (gcp SDK imports are lazy, so this works offline)
    # and the container asserts Protocol conformance as it binds.
    assert isinstance(container.access_audit, AccessAuditPort)
    assert isinstance(container.identity, IdentityPort)
    assert isinstance(container.upstream, UpstreamClientPort)
    assert isinstance(container.bff_signing_key, BffSigningKeyPort)
    assert isinstance(container.subject_token, SubjectTokenPort)


def test_onprem_upstream_fails_fast() -> None:
    import asyncio

    container = build_container(Settings(profile="onprem"))
    with pytest.raises(NotImplementedError):
        asyncio.run(
            container.upstream.forward(method="GET", url="http://x/y", headers={}, content=b"")
        )


def test_onprem_identity_fails_fast() -> None:
    from hex_service_kit.identity import RequestContext

    container = build_container(Settings(profile="onprem"))
    with pytest.raises(NotImplementedError):
        container.identity.resolve(RequestContext(headers={}))


def test_onprem_bff_signing_fails_fast() -> None:
    container = build_container(Settings(profile="onprem"))
    with pytest.raises(NotImplementedError):
        container.bff_signing_key.active_key()


def test_onprem_subject_token_fails_fast() -> None:
    container = build_container(Settings(profile="onprem"))
    with pytest.raises(NotImplementedError):
        container.subject_token.subject_token(subject="a", tenant="b")


def test_the_managed_subject_token_refuses_and_names_the_pending_input() -> None:
    """The dedicated Google OAuth client does not exist yet, so the adapter says so."""
    from journey_portal.ports.subject_token import SubjectTokenUnavailable

    for profile in ("gcp", "platform"):
        container = build_container(
            Settings(
                profile=profile,
                observability_url="http://localhost:8085",
                observability_audience="https://observability-audience.example.test",
            )
        )
        with pytest.raises(SubjectTokenUnavailable, match="dedicated Google OAuth client id"):
            container.subject_token.subject_token(subject="a", tenant="b")


def test_the_managed_signing_key_refuses_without_a_named_kms_key_version() -> None:
    from journey_portal.ports.bff_credentials import SigningKeyUnavailable

    container = build_container(
        Settings(
            profile="gcp",
            observability_url="http://localhost:8085",
            observability_audience="https://observability-audience.example.test",
        )
    )
    with pytest.raises(SigningKeyUnavailable, match="PORTAL_BFF_SIGNING_KEY_VERSION"):
        container.bff_signing_key.active_key()


def test_onprem_access_audit_fails_fast() -> None:
    from journey_portal.domain.models import PortalAccessEvent
    from journey_portal.ports.access_audit import AuditUnavailable

    container = build_container(Settings(profile="onprem"))
    event = PortalAccessEvent(
        event_id="fictional-event",
        occurred_at="2026-07-29T12:00:00+00:00",
        actor_ref="actor-ref",
        tenant_ref="tenant-ref",
        pseudonym_key_id="key-id",
        method="GET",
        action="forward:ui",
        app_id="cdd-sow-research",
    )
    with pytest.raises(AuditUnavailable):
        container.access_audit.append(event)
