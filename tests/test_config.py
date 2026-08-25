"""Settings resolved from the environment (the profile-driven container's only inputs)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from journey_portal import config
from journey_portal.config import Settings
from journey_portal.domain.doc1_broker import BrokerPolicyError

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_upstream_timeout_defaults_to_thirty_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PORTAL_UPSTREAM_TIMEOUT", raising=False)

    assert Settings.load().upstream_timeout_seconds == 30.0


def test_upstream_timeout_is_raised_for_a_long_running_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_UPSTREAM_TIMEOUT", "600")

    assert Settings.load().upstream_timeout_seconds == 600.0


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_upstream_timeout_refuses_instead_of_inheriting_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PORTAL_UPSTREAM_TIMEOUT", value)

    with pytest.raises(ValueError, match="PORTAL_UPSTREAM_TIMEOUT"):
        Settings.load()


@pytest.mark.parametrize(
    "name",
    [
        "PORTAL_REGION",
        "PORTAL_JOURNEYS",
        "PORTAL_LOCAL_AUDIT_DB",
        "PORTAL_BFF_SIGNING_KEY_FILE",
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        "PORTAL_SESSION_SIGNING_KEY",
    ],
)
def test_configured_empty_runtime_values_refuse_instead_of_inheriting_defaults(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv(name, "")
    with pytest.raises(ValueError, match=name):
        Settings.load()


def test_named_deployment_example_never_exports_a_blank_portal_runtime_value() -> None:
    active_blank_runtime_values = [
        line
        for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if re.fullmatch(r"PORTAL_[A-Z0-9_]*=\s*(?:#.*)?", line)
    ]
    assert active_blank_runtime_values == [], (
        "a blank DEPLOY_* value may be an explicit two-stage Terraform marker, but a blank "
        "PORTAL_* runtime value is configured-empty and must not be shipped by the example: "
        f"{active_blank_runtime_values}"
    )


@pytest.mark.parametrize("value", ["not-a-number", "0", "-1"])
def test_an_unusable_upstream_timeout_fails_closed(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PORTAL_UPSTREAM_TIMEOUT", value)

    with pytest.raises(ValueError, match="PORTAL_UPSTREAM_TIMEOUT"):
        Settings.load()


def test_unknown_profile_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTAL_PROFILE", "typo")
    with pytest.raises(ValueError, match="PORTAL_PROFILE"):
        Settings.load()


def test_unapproved_region_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # europe-west1, not us-central1: the latter joined the default allowlist when the portfolio
    # region decision was revised (2026-08-24), so it no longer demonstrates a refusal. The guard
    # is unchanged; only the example of an unapproved region had to move.
    assert "europe-west1" not in config.DEFAULT_ALLOWED_REGIONS
    monkeypatch.setenv("PORTAL_REGION", "europe-west1")
    with pytest.raises(ValueError, match="PORTAL_REGION"):
        Settings.load()


def test_local_profile_seeds_a_loopback_tenant_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PORTAL_TENANT_EMBED_POLICIES_JSON", raising=False)

    settings = Settings.load()

    assert settings.tenant_embed_policies[0].policy_id == "local-demo"
    assert settings.tenant_embed_policies[0].tenant == "*"
    assert "localhost" in settings.tenant_embed_policies[0].hosts


def test_managed_profile_requires_explicit_tenant_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_PROFILE", "gcp")
    monkeypatch.delenv("PORTAL_TENANT_EMBED_POLICIES_JSON", raising=False)

    with pytest.raises(ValueError, match="PORTAL_TENANT_EMBED_POLICIES_JSON"):
        Settings.load()


def test_platform_profile_requires_hrz5_observability_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_PROFILE", "platform")
    monkeypatch.setenv(
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "fictional-bank-v1": {
                    "tenant": "fictional-bank",
                    "hosts": ["journey.fictional-bank.test"],
                    "frame_ancestors": ["'self'"],
                    "cors_origins": [],
                }
            }
        ),
    )
    monkeypatch.delenv("PORTAL_OBSERVABILITY_URL", raising=False)
    monkeypatch.delenv("PORTAL_OBSERVABILITY_AUDIENCE", raising=False)

    with pytest.raises(ValueError, match="PORTAL_OBSERVABILITY_URL"):
        Settings.load()

    monkeypatch.setenv("PORTAL_OBSERVABILITY_URL", "https://hrz5.fictional-bank.test")
    with pytest.raises(ValueError, match="PORTAL_OBSERVABILITY_AUDIENCE"):
        Settings.load()


def test_managed_profile_loads_exact_tenant_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_PROFILE", "gcp")
    monkeypatch.setenv(
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "fictional-bank-v1": {
                    "tenant": "fictional-bank",
                    "hosts": ["journey.fictional-bank.test"],
                    "frame_ancestors": ["'self'", "https://host.fictional-bank.test"],
                    "cors_origins": ["https://host.fictional-bank.test"],
                }
            }
        ),
    )

    policy = Settings.load().tenant_embed_policies[0]

    assert policy.policy_id == "fictional-bank-v1"
    assert policy.tenant == "fictional-bank"


@pytest.mark.parametrize(
    "document",
    [
        {
            "first": {
                "tenant": "fictional-bank",
                "hosts": ["journey.fictional-bank.test"],
                "frame_ancestors": ["*"],
                "cors_origins": [],
            }
        },
        {
            "first": {
                "tenant": "fictional-bank",
                "hosts": ["journey.fictional-bank.test"],
                "frame_ancestors": ["'self'"],
                "cors_origins": [],
            },
            "second": {
                "tenant": "other-bank",
                "hosts": ["journey.fictional-bank.test"],
                "frame_ancestors": ["'self'"],
                "cors_origins": [],
            },
        },
    ],
)
def test_unsafe_tenant_policy_fails_at_settings_load(
    monkeypatch: pytest.MonkeyPatch,
    document: dict[str, object],
) -> None:
    monkeypatch.setenv("PORTAL_PROFILE", "gcp")
    monkeypatch.setenv("PORTAL_TENANT_EMBED_POLICIES_JSON", json.dumps(document))

    with pytest.raises(ValueError, match="wildcard|resolve exactly once"):
        Settings.load()


# --------------------------------------------------------------------------- Doc1 broker settings
def test_a_chosen_local_profile_gets_the_fictional_doc1_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The offline demo needs a complete registration; it is obviously fictional and local-only."""
    monkeypatch.setenv("PORTAL_PROFILE", "local")
    for name in (
        "PORTAL_DOC1_BFF_CLIENT_ID",
        "PORTAL_DOC1_GRANT_ENDPOINT",
        "PORTAL_DOC1_INSTALLATION_ID",
        "PORTAL_PUBLIC_ORIGIN",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.load()
    policy = settings.doc1_broker_policy
    assert policy.portal_origin == "http://127.0.0.1:8110"
    assert policy.bff_client_id.endswith("local-demo")
    assert policy.requested_scopes == ("cdd.embed", "cdd.read")


@pytest.mark.parametrize("profile", ["gcp", "platform"])
def test_a_managed_profile_gets_no_registration_default_at_all(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """A deployment names its own registration; an absent one refuses rather than defaulting."""
    monkeypatch.setenv("PORTAL_PROFILE", profile)
    monkeypatch.setenv("PORTAL_OBSERVABILITY_URL", "https://hrz5.example.test")
    monkeypatch.setenv("PORTAL_OBSERVABILITY_AUDIENCE", "https://hrz5-audience.example.test")
    monkeypatch.setenv(
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "managed": {
                    "tenant": "demo-bank",
                    "hosts": ["portal.example.test"],
                    "frame_ancestors": ["'self'"],
                    "cors_origins": ["https://portal.example.test"],
                }
            }
        ),
    )
    for name in (
        "PORTAL_DOC1_BFF_CLIENT_ID",
        "PORTAL_DOC1_GRANT_ENDPOINT",
        "PORTAL_DOC1_INSTALLATION_ID",
        "PORTAL_PUBLIC_ORIGIN",
        "PORTAL_SESSION_SIGNING_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.load()
    assert settings.bff_client_id == ""
    assert settings.doc1_grant_endpoint == ""
    assert settings.public_origin == ""
    # The CSRF secret is never improvised outside local: an unset one leaves the routes refusing.
    assert settings.session_signing_key == ""
    with pytest.raises(BrokerPolicyError):
        _ = settings.doc1_broker_policy


def test_an_unconsented_run_gets_no_registration_default_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fictional registration is a relaxation, so it keys off the EXPOSURE profile."""
    monkeypatch.delenv("PORTAL_PROFILE", raising=False)
    monkeypatch.setenv(
        "PORTAL_TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "unconsented": {
                    "tenant": "demo-bank",
                    "hosts": ["portal.example.test"],
                    "frame_ancestors": ["'self'"],
                    "cors_origins": ["https://portal.example.test"],
                }
            }
        ),
    )
    for name in ("PORTAL_DOC1_BFF_CLIENT_ID", "PORTAL_PUBLIC_ORIGIN", "PORTAL_SESSION_SIGNING_KEY"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.load()
    assert settings.profile_explicit is False
    assert settings.bff_client_id == ""
    assert settings.public_origin == ""
    assert settings.session_signing_key == ""


def test_an_emptied_scope_list_refuses_rather_than_inheriting_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_PROFILE", "local")
    monkeypatch.setenv("PORTAL_DOC1_REQUESTED_SCOPES", "  ,  ")

    with pytest.raises(ValueError, match="PORTAL_DOC1_REQUESTED_SCOPES"):
        Settings.load()
