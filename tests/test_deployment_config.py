from __future__ import annotations

import json
from pathlib import Path

import pytest

from journey_portal.deployment_config import DeploymentConfigError, load_deployment_config


def _image(name: str, character: str) -> str:
    return (
        f"asia-southeast1-docker.pkg.dev/bank-hrz9-prod-001/portal/{name}@sha256:{character * 64}"
    )


def _valid_values() -> dict[str, str]:
    profile_envs = {
        "doc1": "CDD_PROFILE",
        "doc2": "CREDIT_MEMO_PROFILE",
        "doc3": "CIO_PROFILE",
        "doc4": "TRADE_FINANCE_PROFILE",
        "doc5": "LOAN_DOC_PROFILE",
        "rsk1": "COMPLIANCE_PROFILE",
        "hrz7": "REVIEW_PROFILE",
    }
    embedded = {
        app_id: {
            "ui_image": _image(f"{app_id}-ui", "d"),
            "api_image": _image(f"{app_id}-api", "e"),
            "ui_build_base_path": "/agent" if app_id == "doc1" else f"/apps/{app_id}",
            "ui_secret_env": {"UI_TOKEN": f"{app_id}-ui-token"},
            "api_secret_env": {"API_TOKEN": f"{app_id}-api-token"},
            "api_env": {profile_envs[app_id]: "gcp"},
        }
        for app_id in ("doc1", "doc2", "doc3", "doc4", "doc5", "rsk1", "hrz7")
    }
    return {
        "GCP_PROJECT_ID": "bank-hrz9-prod-001",
        "GCP_REGION": "asia-southeast1",
        "GCP_ALLOWED_REGIONS_JSON": '["asia-southeast1"]',
        "DEPLOY_NAME_PREFIX": "bank-hrz9",
        "DEPLOY_BFF_IMAGE": _image("bff", "a"),
        "DEPLOY_RM_SHELL_IMAGE": _image("rm", "b"),
        "DEPLOY_OPS_SHELL_IMAGE": _image("ops", "c"),
        "DEPLOY_EMBEDDED_APPS_JSON": json.dumps(embedded),
        "DEPLOY_ROLLBACK_IMAGES_JSON": json.dumps(
            {
                "bff": _image("bff", "1"),
                "rm": _image("rm", "2"),
                "ops": _image("ops", "3"),
                **{
                    f"{app_id}-{surface}": _image(f"{app_id}-{surface}", "4")
                    for app_id in ("doc1", "doc2", "doc3", "doc4", "doc5", "rsk1", "hrz7")
                    for surface in ("ui", "api")
                },
            }
        ),
        "DEPLOY_RM_DOMAIN": "rm-journey.bank.internal",
        "DEPLOY_OPS_DOMAIN": "ops-journey.bank.internal",
        "DEPLOY_TENANT_ID": "bank-sg",
        "DEPLOY_TENANT_IDENTITY_DOMAINS_JSON": '["bank.internal"]',
        "DEPLOY_HRZ5_URL": "https://hrz5.bank.internal",
        "DEPLOY_HRZ5_AUDIENCE": "https://hrz5-audience.bank.internal",
        "DEPLOY_DNS_MANAGED_ZONE": "bank-journeys",
        "DEPLOY_TLS_MODE": "google-managed",
        "DEPLOY_IAP_OAUTH_CLIENT_ID": "123-prod.apps.googleusercontent.com",
        "DEPLOY_PORTAL_AUDIT_HMAC_SECRET": "bank-hrz9-portal-audit-hmac",
        "DEPLOY_PORTAL_AUDIT_HMAC_SECRET_VERSION": "7",
        "DEPLOY_IAP_JWT_AUDIENCE": "/projects/123/global/backendServices/456",
        "DEPLOY_IAP_MEMBERS_JSON": '["group:journey-users@bank.internal"]',
        "DEPLOY_FRAME_ANCESTORS_JSON": json.dumps(["'self'", "https://portal.bank.internal"]),
        "DEPLOY_CORS_ORIGINS_JSON": "[]",
        "DEPLOY_NOTIFICATION_CHANNELS_JSON": (
            '["projects/bank-hrz9-prod-001/notificationChannels/123"]'
        ),
        "DEPLOY_APPLY_ORG_POLICIES": "false",
        "DEPLOY_VPC_SC_ACCESS_POLICY_ID": "123456789",
        "DEPLOY_VPC_SC_ENFORCED": "false",
        "DEPLOY_CMEK_ROTATION_PERIOD": "7776000s",
        "DEPLOY_AUDIT_RETENTION_DAYS": "180",
        "DEPLOY_LOCK_AUDIT_BUCKET": "false",
        "DEPLOY_CLOUD_RUN_DELETION_PROTECTION": "true",
        "DEPLOY_TERRAFORM_STATE_BUCKET": "bank-hrz9-prod-state",
        "DEPLOY_TERRAFORM_STATE_PREFIX": "hrz9/production",
        "DEPLOYMENT_OWNER": "deployment@bank.internal",
        "SECURITY_OWNER": "security@bank.internal",
        "DNS_IAP_OWNER": "identity@bank.internal",
        "OPERATIONS_OWNER": "operations@bank.internal",
        "EVIDENCE_APPROVER": "evidence@bank.internal",
    }


def _write(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def _load(tmp_path: Path, values: dict[str, str], secret: str = "unit-test-iap-secret"):
    env_file = tmp_path / ".env"
    secrets_file = tmp_path / ".env.secrets"
    _write(env_file, values)
    _write(secrets_file, {"DEPLOY_IAP_OAUTH_CLIENT_SECRET": secret})
    secrets_file.chmod(0o600)
    return load_deployment_config(env_file, secrets_file)


def test_loads_complete_named_config_and_keeps_secret_out_of_tfvars(tmp_path: Path) -> None:
    config = _load(tmp_path, _valid_values())

    assert config.terraform_inputs["audit_retention_days"] == 180
    assert config.terraform_inputs["lock_audit_bucket"] is False
    assert config.terraform_inputs["cloud_run_deletion_protection"] is True
    assert config.terraform_inputs["vpc_sc_enforced"] is False
    assert config.terraform_inputs["portal_audit_hmac_secret_version"] == "7"
    assert config.terraform_inputs["tenant_embed_policies"]["bank-sg-primary"]["tenant"] == (
        "bank-sg"
    )
    assert config.terraform_inputs["observability_url"] == "https://hrz5.bank.internal"
    assert config.terraform_inputs["observability_audience"] == (
        "https://hrz5-audience.bank.internal"
    )
    assert "iap_oauth2_client_secret" not in config.terraform_inputs


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("GCP_PROJECT_ID", "replace-me-project", "placeholder"),
        ("DEPLOY_BFF_IMAGE", "registry.invalid/bff:latest", "immutable"),
        ("DEPLOY_FRAME_ANCESTORS_JSON", '["*"]', "frame ancestors"),
        (
            "DEPLOY_FRAME_ANCESTORS_JSON",
            '["https://a..bank.internal"]',
            "frame ancestors",
        ),
        (
            "DEPLOY_CORS_ORIGINS_JSON",
            '["https://portal.bank.internal:443"]',
            "CORS origins",
        ),
        (
            "DEPLOY_CORS_ORIGINS_JSON",
            '["https://PORTAL.bank.internal"]',
            "CORS origins",
        ),
        ("DEPLOY_TENANT_ID", "Upper Case", "stable lowercase"),
        ("DEPLOY_HRZ5_URL", "http://hrz5.bank.internal", "lowercase HTTPS origin"),
        ("DEPLOY_HRZ5_AUDIENCE", "https://hrz5.bank.internal/path", "lowercase HTTPS origin"),
        ("DEPLOY_VPC_SC_ACCESS_POLICY_ID", "policy-id", "numeric"),
    ],
)
def test_rejects_unsafe_nonsecret_values(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    values = _valid_values()
    values[key] = value

    with pytest.raises(DeploymentConfigError, match=message):
        _load(tmp_path, values)


def test_rejects_secret_in_nonsecret_file(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_IAP_OAUTH_CLIENT_SECRET"] = "leaked"

    with pytest.raises(DeploymentConfigError, match="secret variables must not"):
        _load(tmp_path, values)


def test_rejects_group_readable_secret_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    secrets_file = tmp_path / ".env.secrets"
    _write(env_file, _valid_values())
    _write(secrets_file, {"DEPLOY_IAP_OAUTH_CLIENT_SECRET": "unit-test-iap-secret"})
    secrets_file.chmod(0o640)

    with pytest.raises(DeploymentConfigError, match="mode 0600"):
        load_deployment_config(env_file, secrets_file)


def test_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    values = _valid_values()
    values["TYPO_PROJECT"] = "bank-hrz9-prod-001"
    with pytest.raises(DeploymentConfigError, match="unknown variables"):
        _load(tmp_path, values)


def test_rejects_an_app_id_the_portal_cannot_mount(tmp_path: Path) -> None:
    """An id outside the known vocabulary is a typo, not a new product.

    The rollback map is completed for the bogus app too, so this test fails on the id itself
    rather than on the rollback contract firing first. Without that, the assertion passes for
    the wrong reason and stops testing what its name says.
    """
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    apps["other"] = json.loads(json.dumps(apps["doc1"]))
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(apps)
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    rollback["other-ui"] = rollback["doc1-ui"]
    rollback["other-api"] = rollback["doc1-api"]
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)

    with pytest.raises(DeploymentConfigError, match="cannot mount"):
        _load(tmp_path, values)


def test_rejects_unknown_embedded_app_key(tmp_path: Path) -> None:
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    apps["doc1"]["unsupported"] = "value"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(apps)

    with pytest.raises(DeploymentConfigError, match="unknown keys"):
        _load(tmp_path, values)


def test_a_partial_journey_portfolio_is_deployable(tmp_path: Path) -> None:
    """One journey app is a valid installation, not a broken one.

    The portal previously demanded all seven on every deployment, which coupled seven
    independently-released repositories into one atomic release and made a single-journey
    installation inexpressible. Five of those seven repositories ship no UI Dockerfile at all
    (verified 2026-08-24), so the rule also could not be satisfied by anyone.
    """
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    single = {"doc1": apps["doc1"]}
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(single)
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {
            "bff": rollback["bff"],
            "rm": rollback["rm"],
            "ops": rollback["ops"],
            "doc1-ui": rollback["doc1-ui"],
            "doc1-api": rollback["doc1-api"],
        }
    )
    config = _load(tmp_path, values)
    assert set(config.terraform_inputs["embedded_apps"]) == {"doc1"}


def test_rollback_must_cover_every_deployed_app(tmp_path: Path) -> None:
    """The guarantee kept from the old rule: nothing deploys without a way back."""
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps({"doc1": apps["doc1"]})
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {"bff": rollback["bff"], "rm": rollback["rm"], "ops": rollback["ops"]}
    )

    with pytest.raises(DeploymentConfigError, match="must exactly cover"):
        _load(tmp_path, values)


def test_an_empty_app_set_is_still_refused(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps({})
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {
            k: v
            for k, v in json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"]).items()
            if k in {"bff", "rm", "ops"}
        }
    )

    with pytest.raises(DeploymentConfigError, match="must not be empty"):
        _load(tmp_path, values)


def test_rejects_wrong_ui_build_base_path(tmp_path: Path) -> None:
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    apps["doc2"]["ui_build_base_path"] = "/"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(apps)

    with pytest.raises(DeploymentConfigError, match="must be built for /apps/doc2"):
        _load(tmp_path, values)


def test_region_is_a_deploy_time_input_within_its_allowlist(tmp_path: Path) -> None:
    """Residency is chosen at deploy time, as it is in every other repository here.

    This previously required literally asia-southeast1 and nothing else, which made the portal
    the one component that could not follow a portfolio region decision without a code change
    — and it did not match the region the launch set actually settled on (us-central1).
    """
    values = _valid_values()
    values["GCP_REGION"] = "australia-southeast1"
    values["GCP_ALLOWED_REGIONS_JSON"] = '["asia-southeast1","australia-southeast1"]'
    config = _load(tmp_path, values)
    assert config.terraform_inputs["region"] == "australia-southeast1"


def test_rejects_a_region_outside_its_own_allowlist(tmp_path: Path) -> None:
    """The allowlist is the control; the region must be inside it."""
    values = _valid_values()
    values["GCP_REGION"] = "europe-west2"
    values["GCP_ALLOWED_REGIONS_JSON"] = '["asia-southeast1"]'

    with pytest.raises(DeploymentConfigError, match="GCP_REGION must be in"):
        _load(tmp_path, values)


def test_rejects_a_repeated_region_in_the_allowlist(tmp_path: Path) -> None:
    values = _valid_values()
    values["GCP_ALLOWED_REGIONS_JSON"] = '["asia-southeast1","asia-southeast1"]'

    with pytest.raises(DeploymentConfigError, match="must not repeat"):
        _load(tmp_path, values)


@pytest.mark.parametrize(
    "channels",
    [
        [
            "projects/bank-hrz9-prod-001/notificationChannels/123",
            "projects/bank-hrz9-prod-001/notificationChannels/123",
        ],
        ["projects/other-project-001/notificationChannels/123"],
    ],
)
def test_rejects_duplicate_or_cross_project_channels(tmp_path: Path, channels: list[str]) -> None:
    values = _valid_values()
    values["DEPLOY_NOTIFICATION_CHANNELS_JSON"] = json.dumps(channels)

    with pytest.raises(DeploymentConfigError, match="distinct|belong"):
        _load(tmp_path, values)


@pytest.mark.parametrize(
    "origin",
    [
        "https://user@portal.bank.internal",
        "https://portal.bank.internal/path",
        "https://portal.bank.internal?query=1",
        "https://*.bank.internal",
        "http://portal.bank.internal",
    ],
)
def test_rejects_non_exact_origins(tmp_path: Path, origin: str) -> None:
    values = _valid_values()
    values["DEPLOY_CORS_ORIGINS_JSON"] = json.dumps([origin])
    with pytest.raises(DeploymentConfigError, match="exact HTTPS origins"):
        _load(tmp_path, values)


def test_rejects_secret_payload_in_embedded_nonsecret_env(tmp_path: Path) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    embedded["doc1"]["api_env"] = {"API_TOKEN": "secret-value"}
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)

    with pytest.raises(DeploymentConfigError, match="api_secret_env"):
        _load(tmp_path, values)


@pytest.mark.parametrize(
    ("map_name", "env_name"),
    [
        ("api_env", "CIO_PROFILE"),
        ("api_env", "K_SERVICE"),
        ("api_secret_env", "CDD_PROFILE"),
        ("api_secret_env", "CDD_IAP_AUDIENCE"),
        ("ui_env", "PORT"),
        ("ui_env", "REVIEW_IAP_AUDIENCE"),
        ("ui_secret_env", "K_REVISION"),
        ("ui_secret_env", "CDD_PROFILE"),
    ],
)
def test_rejects_managed_environment_names_on_every_surface(
    tmp_path: Path, map_name: str, env_name: str
) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    if map_name.endswith("secret_env"):
        embedded["doc1"].setdefault(map_name, {})[env_name] = "managed-collision"
    else:
        embedded["doc1"].setdefault(map_name, {})[env_name] = "value"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)

    with pytest.raises(DeploymentConfigError, match="collides with managed names"):
        _load(tmp_path, values)


@pytest.mark.parametrize("surface", ["ui", "api"])
def test_rejects_plain_and_secret_environment_source_collision(
    tmp_path: Path, surface: str
) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    embedded["doc1"].setdefault(f"{surface}_env", {})["DUPLICATE_SETTING"] = "plain"
    embedded["doc1"].setdefault(f"{surface}_secret_env", {})["DUPLICATE_SETTING"] = "secret-name"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)

    with pytest.raises(DeploymentConfigError, match="plain and secret environment sources"):
        _load(tmp_path, values)


def test_rejects_duplicate_keys_before_json_normalization(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_EMBEDDED_APPS_JSON"] = values["DEPLOY_EMBEDDED_APPS_JSON"].replace(
        '"CDD_PROFILE": "gcp"',
        '"DUPLICATE": "one", "DUPLICATE": "two", "CDD_PROFILE": "gcp"',
        1,
    )

    with pytest.raises(DeploymentConfigError, match="duplicate object key 'DUPLICATE'"):
        _load(tmp_path, values)


def test_secret_like_env_key_detection_is_case_insensitive(tmp_path: Path) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    embedded["doc1"]["api_env"]["databasePassword"] = "not-allowed"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)
    with pytest.raises(DeploymentConfigError, match="api_secret_env"):
        _load(tmp_path, values)


def test_rejects_local_embedded_profile_and_manual_iap_audience(tmp_path: Path) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    embedded["doc2"]["api_env"]["CREDIT_MEMO_PROFILE"] = "local"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)
    with pytest.raises(DeploymentConfigError, match="CREDIT_MEMO_PROFILE"):
        _load(tmp_path, values)

    embedded["doc2"]["api_env"]["CREDIT_MEMO_PROFILE"] = "gcp"
    embedded["doc2"]["api_env"]["CREDIT_MEMO_IAP_AUDIENCE"] = "/guessed"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)
    with pytest.raises(DeploymentConfigError, match="must not override"):
        _load(tmp_path, values)


def test_rejects_placeholder_secret(tmp_path: Path) -> None:
    with pytest.raises(DeploymentConfigError, match="placeholder"):
        _load(tmp_path, _valid_values(), secret="REPLACE_ME")


@pytest.mark.parametrize("retention", ["179", "six-months"])
def test_named_deployment_requires_six_month_minimum_retention(
    tmp_path: Path, retention: str
) -> None:
    values = _valid_values()
    values["DEPLOY_AUDIT_RETENTION_DAYS"] = retention

    with pytest.raises(DeploymentConfigError, match="DEPLOY_AUDIT_RETENTION_DAYS"):
        _load(tmp_path, values)


@pytest.mark.parametrize(
    "rotation",
    ["86399s", "3153600001s", "86400.0000000000s", "24h"],
)
def test_rejects_kms_rotation_outside_supported_duration(tmp_path: Path, rotation: str) -> None:
    values = _valid_values()
    values["DEPLOY_CMEK_ROTATION_PERIOD"] = rotation

    with pytest.raises(DeploymentConfigError, match="DEPLOY_CMEK_ROTATION_PERIOD"):
        _load(tmp_path, values)


def test_accepts_fractional_kms_rotation_within_bounds(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_CMEK_ROTATION_PERIOD"] = "86400.5s"

    assert _load(tmp_path, values).terraform_inputs["cmek_rotation_period"] == "86400.5s"


def test_rejects_vpc_sc_enforcement_until_restricted_egress_exists(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_VPC_SC_ENFORCED"] = "true"
    with pytest.raises(DeploymentConfigError, match="must remain false"):
        _load(tmp_path, values)


def test_bootstrap_requires_empty_members_when_audience_is_empty(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_IAP_JWT_AUDIENCE"] = ""

    with pytest.raises(DeploymentConfigError, match="members require"):
        _load(tmp_path, values)


# --------------------------------------------------------------------------- Doc1 Mode 5
_MODE5_VALUES = {
    "PORTAL_PUBLIC_ORIGIN": "https://portal.bank.internal",
    "PORTAL_DOC1_GRANT_ENDPOINT": "https://doc1.bank.internal/agent/api/v1/embed/grants",
    "PORTAL_DOC1_INSTALLATION_ID": "inst_bank_sg",
    "PORTAL_DOC1_BFF_CLIENT_ID": "hrz9-journey-portal-bff",
    "PORTAL_BFF_SIGNING_KEY_VERSION": (
        "projects/bank-hrz9-prod-001/locations/asia-southeast1/keyRings/hrz9/"
        "cryptoKeys/bff-signing/cryptoKeyVersions/1"
    ),
    "PORTAL_BFF_SIGNING_KID": "bff-signing-1",
}


def _load_with_mode5(
    tmp_path: Path,
    values: dict[str, str],
    *,
    session_key: str = "unit-test-session-signing-key",
):
    env_file = tmp_path / ".env"
    secrets_file = tmp_path / ".env.secrets"
    _write(env_file, values)
    secret_values = {"DEPLOY_IAP_OAUTH_CLIENT_SECRET": "unit-test-iap-secret"}
    if session_key:
        secret_values["PORTAL_SESSION_SIGNING_KEY"] = session_key
    _write(secrets_file, secret_values)
    secrets_file.chmod(0o600)
    return load_deployment_config(env_file, secrets_file)


def test_a_deployment_without_a_mode5_registration_still_validates(tmp_path: Path) -> None:
    """An Hrz9 deployment that fronts no Doc1 Mode 5 installation has nothing to register."""
    assert _load(tmp_path, _valid_values()) is not None


def test_a_complete_mode5_registration_validates(tmp_path: Path) -> None:
    config = _load_with_mode5(tmp_path, {**_valid_values(), **_MODE5_VALUES})
    assert config.values["PORTAL_DOC1_BFF_CLIENT_ID"] == "hrz9-journey-portal-bff"


@pytest.mark.parametrize("omitted", sorted(_MODE5_VALUES))
def test_a_partial_mode5_registration_is_refused(tmp_path: Path, omitted: str) -> None:
    values = {**_valid_values(), **_MODE5_VALUES}
    values.pop(omitted)
    with pytest.raises(DeploymentConfigError, match="Doc1 Mode 5 registration"):
        _load_with_mode5(tmp_path, values)


def test_a_mode5_registration_without_a_session_signing_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DeploymentConfigError, match="PORTAL_SESSION_SIGNING_KEY"):
        _load_with_mode5(tmp_path, {**_valid_values(), **_MODE5_VALUES}, session_key="")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("PORTAL_PUBLIC_ORIGIN", "http://portal.bank.internal", "HTTPS"),
        ("PORTAL_PUBLIC_ORIGIN", "https://portal.bank.internal/", "trailing slash"),
        ("PORTAL_DOC1_GRANT_ENDPOINT", "doc1.bank.internal/grants", "HTTPS"),
        ("PORTAL_DOC1_BFF_CLIENT_ID", "PENDING", "placeholder"),
        ("PORTAL_BFF_SIGNING_KID", "REPLACE_ME", "placeholder"),
        ("PORTAL_BFF_SIGNING_KEY_VERSION", "bff-signing-key", "key VERSION"),
    ],
)
def test_an_unsafe_mode5_value_is_refused(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    values = {**_valid_values(), **_MODE5_VALUES, key: value}
    with pytest.raises(DeploymentConfigError, match=message):
        _load_with_mode5(tmp_path, values)


def test_runtime_allowlist_is_configurable(monkeypatch) -> None:
    """The RUNTIME residency allowlist follows the deployment, not a hardcoded constant."""
    from journey_portal import config as portal_config

    monkeypatch.delenv("PORTAL_ALLOWED_REGIONS", raising=False)
    assert portal_config.resolve_allowed_regions() == portal_config.DEFAULT_ALLOWED_REGIONS

    monkeypatch.setenv("PORTAL_ALLOWED_REGIONS", "us-central1, europe-west2")
    assert portal_config.resolve_allowed_regions() == frozenset({"us-central1", "europe-west2"})


def test_an_empty_runtime_allowlist_is_an_error_not_permission(monkeypatch) -> None:
    """An empty allowlist would disable residency silently; a control a typo can switch off
    is not a control."""
    from journey_portal import config as portal_config

    monkeypatch.setenv("PORTAL_ALLOWED_REGIONS", "")
    with pytest.raises(ValueError, match="set but empty"):
        portal_config.resolve_allowed_regions()


def test_catalog_serves_only_the_deployed_apps() -> None:
    """A journeys config is a CATALOGUE; an installation may deploy a subset of it.

    Added after the 2026-08-24 reference deployment, where the portal loaded all seven apps
    and refused to start on the six that still carried their local ${VAR:-http://127.0.0.1:...}
    defaults — a managed deployment failing on loopback upstreams belonging to journeys it
    never intended to serve.
    """
    from journey_portal.domain.catalog import JourneyCatalog

    raw = {
        "apps": {
            "doc1": {
                "label": "One",
                "ui_upstream": "https://doc1-ui.example.com",
                "api_upstream": "https://doc1-api.example.com",
                "canonical_mount_path": "/agent",
            },
            "doc2": {
                "label": "Two",
                "ui_upstream": "http://127.0.0.1:3102",
                "api_upstream": "http://127.0.0.1:8093",
            },
        },
        "journeys": {
            "rm": {"label": "RM", "blurb": "b", "apps": ["doc1", "doc2"]},
            "ops": {"label": "Ops", "blurb": "b", "apps": ["doc2"]},
        },
    }

    catalog = JourneyCatalog.from_mapping(raw, only_apps=frozenset({"doc1"}))
    assert set(catalog.apps) == {"doc1"}
    # The rm journey keeps its deployed app...
    assert catalog.journeys["rm"].app_ids == ("doc1",)
    # ...and the ops journey, whose every app belongs to another installation, is dropped
    # rather than shown as an empty dead end.
    assert "ops" not in catalog.journeys


def test_an_app_named_by_the_deployment_but_absent_from_config_is_an_error() -> None:
    """Silently dropping it would hide a typo AND a genuinely misconfigured upstream."""
    from journey_portal.domain.catalog import JourneyCatalog
    from journey_portal.domain.errors import JourneyConfigError

    raw = {
        "apps": {
            "doc1": {
                "label": "One",
                "ui_upstream": "https://doc1-ui.example.com",
                "api_upstream": "https://doc1-api.example.com",
                "canonical_mount_path": "/agent",
            }
        },
        "journeys": {"rm": {"label": "RM", "blurb": "b", "apps": ["doc1"]}},
    }

    with pytest.raises(JourneyConfigError, match="not present in the journeys config"):
        JourneyCatalog.from_mapping(raw, only_apps=frozenset({"doc1", "doc9"}))


def test_a_deployment_with_no_serviceable_journey_is_an_error() -> None:
    from journey_portal.domain.catalog import JourneyCatalog
    from journey_portal.domain.errors import JourneyConfigError

    raw = {
        "apps": {
            "doc1": {
                "label": "One",
                "ui_upstream": "https://doc1-ui.example.com",
                "api_upstream": "https://doc1-api.example.com",
                "canonical_mount_path": "/agent",
            },
            "doc2": {
                "label": "Two",
                "ui_upstream": "https://doc2-ui.example.com",
                "api_upstream": "https://doc2-api.example.com",
            },
        },
        "journeys": {"ops": {"label": "Ops", "blurb": "b", "apps": ["doc2"]}},
    }

    with pytest.raises(JourneyConfigError, match="no journey has any deployed app"):
        JourneyCatalog.from_mapping(raw, only_apps=frozenset({"doc1"}))


def test_deletion_protection_is_a_reviewed_input_not_a_generated_file_edit(tmp_path: Path) -> None:
    """The rendered tfvars must CARRY the setting, in both states.

    It was absent before, so the Terraform default (true) won and the only way to deploy with it
    off was to hand-edit the ignored generated file that `render` overwrites. That edit survived
    until the next render and then reverted silently, which is how a live deployment and its
    reviewed inputs disagreed with nothing to show it.
    """

    values = _valid_values()
    values["DEPLOY_CLOUD_RUN_DELETION_PROTECTION"] = "false"
    assert _load(tmp_path, values).terraform_inputs["cloud_run_deletion_protection"] is False

    values["DEPLOY_CLOUD_RUN_DELETION_PROTECTION"] = "true"
    assert _load(tmp_path, values).terraform_inputs["cloud_run_deletion_protection"] is True


def test_missing_deletion_protection_is_refused(tmp_path: Path) -> None:
    values = _valid_values()
    del values["DEPLOY_CLOUD_RUN_DELETION_PROTECTION"]
    with pytest.raises(DeploymentConfigError):
        _load(tmp_path, values)


def test_identity_domains_map_onto_the_reviewed_tenant(tmp_path: Path) -> None:
    """The mapping that stops a Workspace domain being compared against a tenant LABEL.

    Without it the managed identity adapter set ``tenant`` to the assertion's hosted domain and
    the embed registry declared the tenant as ``bank-sg``. Those are different strings on every
    real deployment, so the host/tenant check denied every request on a deployment whose inputs
    were otherwise correct, and did so with a message about embedding policy.
    """

    values = _valid_values()
    values["DEPLOY_TENANT_IDENTITY_DOMAINS_JSON"] = '["bank.internal", "svc.bank.internal"]'
    inputs = _load(tmp_path, values).terraform_inputs
    assert inputs["tenant_by_identity_domain"] == {
        "bank.internal": "bank-sg",
        "svc.bank.internal": "bank-sg",
    }
    declared = {policy["tenant"] for policy in inputs["tenant_embed_policies"].values()}
    assert set(inputs["tenant_by_identity_domain"].values()) <= declared, (
        "every mapped tenant must have a reviewed embed policy, or a request resolves onto a "
        "tenant boundary nobody wrote down"
    )


@pytest.mark.parametrize(
    "domains",
    ["[]", '[""]', '["not a domain"]', '["bank.internal", "BANK.INTERNAL"]', '"bank.internal"'],
    ids=["empty", "blank-entry", "not-a-domain", "duplicate-after-casefold", "not-a-list"],
)
def test_a_half_configured_identity_domain_list_is_refused(tmp_path: Path, domains: str) -> None:
    values = _valid_values()
    values["DEPLOY_TENANT_IDENTITY_DOMAINS_JSON"] = domains
    with pytest.raises(DeploymentConfigError):
        _load(tmp_path, values)
