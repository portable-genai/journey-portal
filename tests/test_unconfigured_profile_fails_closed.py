"""An unset ``PORTAL_PROFILE`` is refused, not read as consent to the ``local`` posture.

Before this fix the profile was resolved twice with the same permissive default:
``Settings.load`` used ``os.environ.get("PORTAL_PROFILE", "local")`` and ``api/app.py`` had its
own unvalidated copy. A process started with the variable missing therefore served the whole
``local`` posture nobody had chosen, and the most permissive object in the repo came with it: the
seeded tenant embed policy whose tenant is the wildcard ``*``, which matches EVERY verified
tenant on the loopback hosts.

The refusal is total here rather than partial, and that follows from the domain rather than from
taste: ``TenantEmbedPolicyService`` requires at least one policy, the only policy an unconfigured
run could have is the wildcard relaxation it must not be granted, so a portal with no chosen
profile has no reviewed embed registry and cannot serve. It refuses while LOADING SETTINGS,
before any credential is inspected and with no cloud SDK involved.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from journey_portal.adapters.local.identity import (
    LocalIdentityAdapter,
    LocalPersonaProfileError,
)
from journey_portal.api import app as app_module
from journey_portal.config import (
    UNCONSENTED_PROFILE,
    ProfileNotConfigured,
    Settings,
    resolve_profile,
)


@pytest.fixture
def no_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORTAL_PROFILE", raising=False)
    monkeypatch.delenv("PORTAL_TENANT_EMBED_POLICIES_JSON", raising=False)


def test_loading_settings_refuses_the_wildcard_tenant_policy(no_profile: None) -> None:
    with pytest.raises(ProfileNotConfigured) as excinfo:
        Settings.load()
    message = str(excinfo.value)
    assert "PORTAL_PROFILE" in message
    assert "PORTAL_TENANT_EMBED_POLICIES_JSON" in message


def test_a_deliberate_local_still_gets_the_seeded_demo_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline demo posture is unchanged; only the unchosen one is refused."""
    monkeypatch.setenv("PORTAL_PROFILE", "local")
    monkeypatch.delenv("PORTAL_TENANT_EMBED_POLICIES_JSON", raising=False)
    settings = Settings.load()
    assert settings.profile_explicit is True
    assert [p.tenant for p in settings.tenant_embed_policies] == ["*"]


def test_a_reviewed_registry_is_honoured_without_any_profile_relaxation(
    no_profile: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who supplies the registry does not need the profile to grant a wildcard."""
    monkeypatch.setenv(
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        '{"acme": {"tenant": "acme", "hosts": ["portal.example"], '
        '"frame_ancestors": ["\'self\'"], "cors_origins": ["https://portal.example"]}}',
    )
    settings = Settings.load()
    assert settings.profile_explicit is False
    assert [p.tenant for p in settings.tenant_embed_policies] == ["acme"]


def test_a_wildcard_registry_is_refused_without_a_deliberate_local(
    no_profile: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wildcard PERMISSION keys off the exposure profile, not the bound adapter family."""
    monkeypatch.setenv(
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        '{"anyone": {"tenant": "*", "hosts": ["portal.example"], '
        '"frame_ancestors": ["\'self\'"], "cors_origins": ["https://portal.example"]}}',
    )
    with pytest.raises(ValueError, match="wildcard tenant policy"):
        Settings.load()


def test_the_container_turns_the_refusal_into_a_503_naming_the_variable(
    no_profile: None,
) -> None:
    app_module._container.cache_clear()
    try:
        with pytest.raises(HTTPException) as excinfo:
            app_module._container()
        assert excinfo.value.status_code == 503
        assert "PORTAL_PROFILE" in str(excinfo.value.detail)
    finally:
        app_module._container.cache_clear()


def test_a_non_profile_config_error_is_a_named_503_not_a_bare_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chosen profile can still be misconfigured; that must be a 503, never an unhandled 500.

    The profile string is validated at import, so the container only ever sees the OTHER
    configuration errors here (a region outside the allowlist, a bad timeout, malformed embed
    JSON). Before the fix these escaped ``_container`` as a bare 500 because it caught only
    ``ProfileNotConfigured``.
    """
    monkeypatch.setenv("PORTAL_PROFILE", "local")
    monkeypatch.setenv("PORTAL_REGION", "europe-west1")  # outside the residency allowlist
    app_module._container.cache_clear()
    try:
        with pytest.raises(HTTPException) as excinfo:
            app_module._container()
        assert excinfo.value.status_code == 503
        assert "PORTAL_REGION" in str(excinfo.value.detail)
    finally:
        app_module._container.cache_clear()


def test_seeded_personas_refuse_an_inherited_local_profile() -> None:
    """Defence in depth: the personas are injected into every embedded app, so they need consent."""
    with pytest.raises(LocalPersonaProfileError, match="PORTAL_PROFILE"):
        LocalIdentityAdapter(Settings(profile="local", profile_explicit=False))


def test_seeded_personas_still_serve_a_deliberate_local_run() -> None:
    assert LocalIdentityAdapter(Settings(profile="local")).personas()


def test_the_two_directions_disagree_on_purpose() -> None:
    choice = resolve_profile({})
    assert choice.exposure_profile == UNCONSENTED_PROFILE
    assert choice.bind_profile == "local"
