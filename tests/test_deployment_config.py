from __future__ import annotations

import json
from pathlib import Path

import pytest

from journey_portal.config import load_journeys_mapping
from journey_portal.deployment_config import (
    OPTIONAL_NONSECRET_KEYS,
    REQUIRED_NONSECRET_KEYS,
    DeploymentConfigError,
    load_deployment_config,
    load_env_file,
)

_JOURNEYS_FILE = Path("config/journeys.yaml")


def _image(name: str, character: str) -> str:
    return (
        f"asia-southeast1-docker.pkg.dev/bank-hrz9-prod-001/portal/{name}@sha256:{character * 64}"
    )


def _shell(journey: str, character: str = "b") -> dict[str, str]:
    return {
        "journey": journey,
        "image": _image(journey, character),
        "domain": f"{journey}-journey.bank.internal",
    }


def _journey_of(app_id: str) -> str:
    """The journey key a catalog app belongs to, from the catalog rather than a second copy.

    A deployment may only publish a shell whose journey has a deployed app, so a fixture that
    deploys one app has to name that app's own journey. Reading it here means a catalog change
    that moves an app between journeys does not leave these fixtures asserting the old shape.
    """
    journeys = load_journeys_mapping(_JOURNEYS_FILE)["journeys"]
    for key, journey in journeys.items():
        if app_id in journey["apps"]:
            return str(key)
    raise AssertionError(f"{app_id} belongs to no journey in {_JOURNEYS_FILE}")


def _valid_values() -> dict[str, str]:
    profile_envs = {
        "cdd-sow-research": "CDD_PROFILE",
        "credit-memo-drafting": "CREDIT_MEMO_PROFILE",
        "cio-advisory": "CIO_PROFILE",
        "trade-finance-checker": "TRADE_FINANCE_PROFILE",
        "loan-document-intelligence": "LOAN_DOC_PROFILE",
        "compliance-advisory": "COMPLIANCE_PROFILE",
        "human-review-console": "REVIEW_PROFILE",
    }
    embedded = {
        app_id: {
            "ui_image": _image(f"{app_id}-ui", "d"),
            "api_image": _image(f"{app_id}-api", "e"),
            "ui_build_base_path": "/agent" if app_id == "cdd-sow-research" else f"/apps/{app_id}",
            "ui_secret_env": {"UI_TOKEN": f"{app_id}-ui-token"},
            "api_secret_env": {"API_TOKEN": f"{app_id}-api-token"},
            "api_env": {profile_envs[app_id]: "gcp"},
        }
        for app_id in (
            "cdd-sow-research",
            "credit-memo-drafting",
            "cio-advisory",
            "trade-finance-checker",
            "loan-document-intelligence",
            "compliance-advisory",
            "human-review-console",
        )
    }
    return {
        "GCP_PROJECT_ID": "bank-hrz9-prod-001",
        "GCP_REGION": "asia-southeast1",
        "GCP_ALLOWED_REGIONS_JSON": '["asia-southeast1"]',
        "DEPLOY_NAME_PREFIX": "bank-hrz9",
        "DEPLOY_BFF_IMAGE": _image("bff", "a"),
        "DEPLOY_SHELLS_JSON": json.dumps(
            [
                {"journey": "rm", "image": _image("rm", "b"), "domain": "rm-journey.bank.internal"},
                {
                    "journey": "ops",
                    "image": _image("ops", "c"),
                    "domain": "ops-journey.bank.internal",
                },
            ]
        ),
        "DEPLOY_EMBEDDED_APPS_JSON": json.dumps(embedded),
        "DEPLOY_ROLLBACK_IMAGES_JSON": json.dumps(
            {
                "bff": _image("bff", "1"),
                "rm": _image("rm", "2"),
                "ops": _image("ops", "3"),
                **{
                    f"{app_id}-{surface}": _image(f"{app_id}-{surface}", "4")
                    for app_id in (
                        "cdd-sow-research",
                        "credit-memo-drafting",
                        "cio-advisory",
                        "trade-finance-checker",
                        "loan-document-intelligence",
                        "compliance-advisory",
                        "human-review-console",
                    )
                    for surface in ("ui", "api")
                },
            }
        ),
        "DEPLOY_TENANT_ID": "bank-sg",
        "DEPLOY_TENANT_IDENTITY_DOMAINS_JSON": '["bank.internal"]',
        "DEPLOY_OBSERVABILITY_URL": "https://observability.bank.internal",
        "DEPLOY_OBSERVABILITY_AUDIENCE": "https://observability-audience.bank.internal",
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
        "DEPLOY_RUN_MIN_INSTANCES": "1",
        "DEPLOY_LB_LOG_SAMPLE_RATE": "1",
        "DEPLOY_NAT_LOG_FILTER": "ALL",
        "DEPLOY_PRODUCTION_EDGE_ENABLED": "true",
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
    assert config.terraform_inputs["observability_url"] == "https://observability.bank.internal"
    assert config.terraform_inputs["observability_audience"] == (
        "https://observability-audience.bank.internal"
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
        (
            "DEPLOY_OBSERVABILITY_URL",
            "http://observability.bank.internal",
            "lowercase HTTPS origin",
        ),
        (
            "DEPLOY_OBSERVABILITY_AUDIENCE",
            "https://observability.bank.internal/path",
            "lowercase HTTPS origin",
        ),
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
    apps["other"] = json.loads(json.dumps(apps["cdd-sow-research"]))
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(apps)
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    rollback["other-ui"] = rollback["cdd-sow-research-ui"]
    rollback["other-api"] = rollback["cdd-sow-research-api"]
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)

    with pytest.raises(DeploymentConfigError, match="cannot mount"):
        _load(tmp_path, values)


def test_rejects_unknown_embedded_app_key(tmp_path: Path) -> None:
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    apps["cdd-sow-research"]["unsupported"] = "value"
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
    single = {"cdd-sow-research": apps["cdd-sow-research"]}
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(single)
    # One journey's worth of apps is one shell's worth of hosts: the Ops shell goes with the Ops
    # apps, rather than being published with nothing of its own to show.
    values["DEPLOY_SHELLS_JSON"] = json.dumps([_shell("rm")])
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {
            "bff": rollback["bff"],
            "rm": rollback["rm"],
            "cdd-sow-research-ui": rollback["cdd-sow-research-ui"],
            "cdd-sow-research-api": rollback["cdd-sow-research-api"],
        }
    )
    config = _load(tmp_path, values)
    assert set(config.terraform_inputs["embedded_apps"]) == {"cdd-sow-research"}
    assert [shell["journey"] for shell in config.terraform_inputs["shells"]] == ["rm"]


def _single_app_values(app_id: str, api_env: dict[str, str]) -> dict[str, str]:
    """A complete config deploying one app on its own journey's shell, rolled back exactly."""
    values = _valid_values()
    app = {
        "ui_image": _image(f"{app_id}-ui", "d"),
        "api_image": _image(f"{app_id}-api", "e"),
        "ui_build_base_path": f"/apps/{app_id}",
        "api_env": api_env,
    }
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps({app_id: app})
    journey = _journey_of(app_id)
    values["DEPLOY_SHELLS_JSON"] = json.dumps([_shell(journey)])
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {
            "bff": rollback["bff"],
            journey: _image(journey, "2"),
            f"{app_id}-ui": _image(f"{app_id}-ui", "4"),
            f"{app_id}-api": _image(f"{app_id}-api", "4"),
        }
    )
    return values


def test_marketing_compliance_gate_deploys_on_its_managed_profile(tmp_path: Path) -> None:
    values = _single_app_values("marketing-compliance-gate", {"MKT_GOV_PROFILE": "gcp"})

    config = _load(tmp_path, values)

    assert set(config.terraform_inputs["embedded_apps"]) == {"marketing-compliance-gate"}


@pytest.mark.parametrize(
    ("api_env", "refusal"),
    [
        ({}, "must set MKT_GOV_PROFILE to gcp or platform"),
        ({"MKT_GOV_PROFILE": "local"}, "must set MKT_GOV_PROFILE to gcp or platform"),
        (
            {"MKT_GOV_PROFILE": "gcp", "MKT_GOV_IAP_AUDIENCE": "/guessed"},
            "must not override Terraform-injected MKT_GOV_IAP_AUDIENCE",
        ),
    ],
)
def test_marketing_compliance_gate_without_its_managed_env_is_refused(
    tmp_path: Path, api_env: dict[str, str], refusal: str
) -> None:
    values = _single_app_values("marketing-compliance-gate", api_env)

    with pytest.raises(DeploymentConfigError, match=refusal):
        _load(tmp_path, values)


@pytest.mark.parametrize("app_id", ["performance-marketing-optimisation", "complaints-review"])
def test_a_catalog_app_with_no_managed_env_mapping_is_not_deployable(
    tmp_path: Path, app_id: str
) -> None:
    """Mountable locally is not deployable: an app needs its profile and audience mapped."""
    values = _single_app_values(app_id, {})

    with pytest.raises(DeploymentConfigError, match=f"cannot mount: {app_id}"):
        _load(tmp_path, values)


def test_rollback_must_cover_every_deployed_app(tmp_path: Path) -> None:
    """The guarantee kept from the old rule: nothing deploys without a way back."""
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps({"cdd-sow-research": apps["cdd-sow-research"]})
    values["DEPLOY_SHELLS_JSON"] = json.dumps([_shell("rm")])
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {"bff": rollback["bff"], "rm": rollback["rm"]}
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


# --------------------------------------------------------------------------- #
# The shells a deployment publishes: one entry per persona host, not a fixed pair.
# --------------------------------------------------------------------------- #


def _marketing_values() -> dict[str, str]:
    """The reference shape plus a third host for the Marketing journey.

    The Marketing journey was in the catalog from the day the persona journeys landed and no
    deployable shell could publish it, because the renderer took exactly two shell images and
    two domains. This is the configuration that ends that.
    """
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    apps["marketing-compliance-gate"] = {
        "ui_image": _image("marketing-compliance-gate-ui", "d"),
        "api_image": _image("marketing-compliance-gate-api", "e"),
        "ui_build_base_path": "/apps/marketing-compliance-gate",
        "api_env": {"MKT_GOV_PROFILE": "gcp"},
    }
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(apps)
    values["DEPLOY_SHELLS_JSON"] = json.dumps(
        [_shell("rm"), _shell("ops", "c"), _shell("mkt", "f")]
    )
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    rollback["mkt"] = _image("mkt", "3")
    rollback["marketing-compliance-gate-ui"] = _image("marketing-compliance-gate-ui", "4")
    rollback["marketing-compliance-gate-api"] = _image("marketing-compliance-gate-api", "4")
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)
    return values


def test_a_third_shell_publishes_the_marketing_journey(tmp_path: Path) -> None:
    config = _load(tmp_path, _marketing_values())

    shells = config.terraform_inputs["shells"]
    # ORDER, not membership: the managed certificate's domain list is this list, and the
    # provider treats that list as ordered and ForceNew, so a reordering replaces the
    # certificate exactly as adding a domain does. The two existing hosts keep their places
    # and the new one is appended.
    assert [shell["journey"] for shell in shells] == ["rm", "ops", "mkt"]
    assert shells[2]["domain"] == "mkt-journey.bank.internal"
    assert shells[2]["image"] == _image("mkt", "f")
    # The tenant registry follows the shells: every routed hostname resolves to one policy, so
    # the third host is admitted rather than denied as an unregistered tenant host.
    policy = config.terraform_inputs["tenant_embed_policies"]["bank-sg-primary"]
    assert policy["hosts"] == [
        "rm-journey.bank.internal",
        "ops-journey.bank.internal",
        "mkt-journey.bank.internal",
    ]
    assert "rm_shell_image" not in config.terraform_inputs
    assert "rm_domain" not in config.terraform_inputs


def test_a_third_shell_needs_its_own_rollback_component(tmp_path: Path) -> None:
    """A host with no recorded previous digest cannot be rolled back, so it cannot deploy."""
    values = _marketing_values()
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    del rollback["mkt"]
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)

    with pytest.raises(DeploymentConfigError, match="must exactly cover"):
        _load(tmp_path, values)


def test_rejects_a_shell_whose_journey_has_no_deployed_app(tmp_path: Path) -> None:
    """The hazard this check exists for: a healthy page under the wrong persona's hostname.

    The BFF drops a journey none of whose apps are mounted, and the React shell falls back to
    the first journey in the catalog when the one it was built for is absent. So a Marketing
    host with no Marketing app serves the RM journey and looks perfectly well.

    Note what it takes to construct this: `human-review-console` belongs to four journeys, so
    deploying it alone makes four shells publishable. The refusal is reachable only for a
    journey none of whose apps are deployed at all, which is exactly the case it is for.
    """
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps({"cdd-sow-research": apps["cdd-sow-research"]})
    values["DEPLOY_SHELLS_JSON"] = json.dumps([_shell("rm"), _shell("mkt", "f")])
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(
        {
            "bff": rollback["bff"],
            "rm": rollback["rm"],
            "mkt": _image("mkt", "3"),
            "cdd-sow-research-ui": rollback["cdd-sow-research-ui"],
            "cdd-sow-research-api": rollback["cdd-sow-research-api"],
        }
    )

    with pytest.raises(DeploymentConfigError, match="none of its journey's apps"):
        _load(tmp_path, values)


def test_rejects_a_shell_for_a_journey_the_catalog_does_not_define(tmp_path: Path) -> None:
    values = _valid_values()
    values["DEPLOY_SHELLS_JSON"] = json.dumps([_shell("rm"), _shell("hr", "f")])
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    del rollback["ops"]
    rollback["hr"] = _image("hr", "3")
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)

    with pytest.raises(DeploymentConfigError, match="not a journey the journeys config defines"):
        _load(tmp_path, values)


def test_rejects_two_shells_on_one_hostname(tmp_path: Path) -> None:
    values = _valid_values()
    shells = json.loads(values["DEPLOY_SHELLS_JSON"])
    shells[1]["domain"] = shells[0]["domain"]
    values["DEPLOY_SHELLS_JSON"] = json.dumps(shells)

    with pytest.raises(DeploymentConfigError, match="twice; each shell owns its root path"):
        _load(tmp_path, values)


def test_rejects_one_journey_served_by_two_shells(tmp_path: Path) -> None:
    values = _valid_values()
    shells = json.loads(values["DEPLOY_SHELLS_JSON"])
    shells[1]["journey"] = "rm"
    values["DEPLOY_SHELLS_JSON"] = json.dumps(shells)
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    del rollback["ops"]
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)

    with pytest.raises(DeploymentConfigError, match="twice; one shell serves one journey"):
        _load(tmp_path, values)


@pytest.mark.parametrize(
    ("mutate", "refusal"),
    [
        ("[]", "non-empty JSON array"),
        ('{"rm": {}}', "must contain a JSON list"),
        ("[1]", "must be a JSON object"),
        (
            '[{"journey": "rm", "image": "registry.invalid/rm@sha256:' + "b" * 64 + '"}]',
            "exactly the keys journey, image and domain",
        ),
        (
            '[{"journey": "rm", "image": "registry.invalid/rm:latest", '
            '"domain": "rm.bank.internal"}]',
            "immutable @sha256 digest",
        ),
        (
            '[{"journey": "rm", "image": "registry.invalid/rm@sha256:'
            + "b" * 64
            + '", "domain": "https://rm.bank.internal"}]',
            "must be a DNS hostname",
        ),
        (
            '[{"journey": "RM", "image": "registry.invalid/rm@sha256:'
            + "b" * 64
            + '", "domain": "rm.bank.internal"}]',
            "is not a short journey key",
        ),
    ],
)
def test_rejects_an_unusable_shell_list(tmp_path: Path, mutate: str, refusal: str) -> None:
    values = _valid_values()
    values["DEPLOY_SHELLS_JSON"] = mutate
    rollback = json.loads(values["DEPLOY_ROLLBACK_IMAGES_JSON"])
    del rollback["ops"]
    values["DEPLOY_ROLLBACK_IMAGES_JSON"] = json.dumps(rollback)

    with pytest.raises(DeploymentConfigError, match=refusal):
        _load(tmp_path, values)


def test_the_example_env_file_names_exactly_the_variables_the_loader_requires() -> None:
    """A template that cannot be copied is not a template.

    The loader refuses a missing required key and an unknown key alike, so either direction of
    drift between this module and `.env.example` makes the documented first step --
    `cp .env.example .env` then `deployment_config.py check` -- fail on something the reader
    cannot see. It did: `DEPLOY_PRODUCTION_EDGE_ENABLED` was required and absent from the example
    until 2026-09-12, and nothing reported it because nothing compared the two.
    """
    example = load_env_file(Path(".env.example"))

    assert sorted(REQUIRED_NONSECRET_KEYS - example.keys()) == []
    assert sorted(example.keys() - REQUIRED_NONSECRET_KEYS - OPTIONAL_NONSECRET_KEYS) == []
    # And the shells it ships are the two the reference deployment publishes, in that order.
    shells = json.loads(example["DEPLOY_SHELLS_JSON"])
    assert [shell["journey"] for shell in shells] == ["rm", "ops"]


@pytest.mark.parametrize(
    "retired",
    ["DEPLOY_RM_SHELL_IMAGE", "DEPLOY_OPS_SHELL_IMAGE", "DEPLOY_RM_DOMAIN", "DEPLOY_OPS_DOMAIN"],
)
def test_an_env_file_still_naming_a_retired_shell_variable_is_told_what_replaced_it(
    tmp_path: Path, retired: str
) -> None:
    """Refused BY NAME, not as a generic unknown: an unknown-key error says nothing useful."""
    values = _valid_values()
    values[retired] = "rm-journey.bank.internal"

    with pytest.raises(DeploymentConfigError, match="DEPLOY_SHELLS_JSON"):
        _load(tmp_path, values)


def test_rejects_wrong_ui_build_base_path(tmp_path: Path) -> None:
    values = _valid_values()
    apps = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    apps["credit-memo-drafting"]["ui_build_base_path"] = "/"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(apps)

    with pytest.raises(DeploymentConfigError, match="must be built for /apps/credit-memo-drafting"):
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
    embedded["cdd-sow-research"]["api_env"] = {"API_TOKEN": "secret-value"}
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
        embedded["cdd-sow-research"].setdefault(map_name, {})[env_name] = "managed-collision"
    else:
        embedded["cdd-sow-research"].setdefault(map_name, {})[env_name] = "value"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)

    with pytest.raises(DeploymentConfigError, match="collides with managed names"):
        _load(tmp_path, values)


@pytest.mark.parametrize("surface", ["ui", "api"])
def test_rejects_plain_and_secret_environment_source_collision(
    tmp_path: Path, surface: str
) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    embedded["cdd-sow-research"].setdefault(f"{surface}_env", {})["DUPLICATE_SETTING"] = "plain"
    embedded["cdd-sow-research"].setdefault(f"{surface}_secret_env", {})["DUPLICATE_SETTING"] = (
        "secret-name"
    )
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
    embedded["cdd-sow-research"]["api_env"]["databasePassword"] = "not-allowed"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)
    with pytest.raises(DeploymentConfigError, match="api_secret_env"):
        _load(tmp_path, values)


def test_rejects_local_embedded_profile_and_manual_iap_audience(tmp_path: Path) -> None:
    values = _valid_values()
    embedded = json.loads(values["DEPLOY_EMBEDDED_APPS_JSON"])
    embedded["credit-memo-drafting"]["api_env"]["CREDIT_MEMO_PROFILE"] = "local"
    values["DEPLOY_EMBEDDED_APPS_JSON"] = json.dumps(embedded)
    with pytest.raises(DeploymentConfigError, match="CREDIT_MEMO_PROFILE"):
        _load(tmp_path, values)

    embedded["credit-memo-drafting"]["api_env"]["CREDIT_MEMO_PROFILE"] = "gcp"
    embedded["credit-memo-drafting"]["api_env"]["CREDIT_MEMO_IAP_AUDIENCE"] = "/guessed"
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


@pytest.mark.parametrize("floor", ["-1", "101", "one", ""])
def test_rejects_an_instance_floor_outside_supported_bounds(tmp_path: Path, floor: str) -> None:
    values = _valid_values()
    values["DEPLOY_RUN_MIN_INSTANCES"] = floor

    with pytest.raises(DeploymentConfigError, match="DEPLOY_RUN_MIN_INSTANCES"):
        _load(tmp_path, values)


@pytest.mark.parametrize("rate", ["-0.1", "1.1", "most", ""])
def test_rejects_a_load_balancer_sample_rate_outside_zero_to_one(tmp_path: Path, rate: str) -> None:
    values = _valid_values()
    values["DEPLOY_LB_LOG_SAMPLE_RATE"] = rate

    with pytest.raises(DeploymentConfigError, match="DEPLOY_LB_LOG_SAMPLE_RATE"):
        _load(tmp_path, values)


@pytest.mark.parametrize("nat_filter", ["ERRORS", "all", "NONE", ""])
def test_rejects_an_unknown_nat_log_filter(tmp_path: Path, nat_filter: str) -> None:
    # Refused HERE as well as in Terraform: an operator edits this file without an apply, and
    # an apply happens without this file. A rule enforced on one side of that boundary is
    # enforced on neither.
    values = _valid_values()
    values["DEPLOY_NAT_LOG_FILTER"] = nat_filter

    with pytest.raises(DeploymentConfigError, match="DEPLOY_NAT_LOG_FILTER"):
        _load(tmp_path, values)


def test_the_cost_posture_reaches_terraform_as_the_deployment_stated_it(tmp_path: Path) -> None:
    # The point of the whole change: a deployment that declines the floor must actually reach
    # Terraform having declined it. Rendering it back as the code default is the failure this
    # asserts against, and it is exactly what happened to the audit lock in the sibling stack.
    values = _valid_values()
    values["DEPLOY_RUN_MIN_INSTANCES"] = "0"
    values["DEPLOY_LB_LOG_SAMPLE_RATE"] = "0.1"
    values["DEPLOY_NAT_LOG_FILTER"] = "ERRORS_ONLY"
    values["DEPLOY_PRODUCTION_EDGE_ENABLED"] = "false"

    config = _load(tmp_path, values)

    assert config.terraform_inputs["runtime_min_instances"] == 0
    assert config.terraform_inputs["lb_log_sample_rate"] == 0.1
    assert config.terraform_inputs["nat_log_filter"] == "ERRORS_ONLY"
    assert config.terraform_inputs["production_edge_enabled"] is False


def test_an_edge_the_deployment_declines_does_not_return_through_the_code_default(
    tmp_path: Path,
) -> None:
    # The edge is the one cost-posture input whose Terraform default and whose declining value
    # are the SAME literal (false), so an input that never reaches Terraform still produces the
    # declined edge and the test above would pass while the wiring was missing. Asserting the
    # accepted direction is what actually proves the value is carried: nothing but a real
    # mapping can turn a stated true into a built edge.
    values = _valid_values()
    values["DEPLOY_PRODUCTION_EDGE_ENABLED"] = "true"

    config = _load(tmp_path, values)

    assert config.terraform_inputs["production_edge_enabled"] is True


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


# --------------------------------------------------------------------------- cdd-sow-research Mode
# 5
_MODE5_VALUES = {
    "PORTAL_PUBLIC_ORIGIN": "https://portal.bank.internal",
    "PORTAL_DOC1_GRANT_ENDPOINT": "https://cdd-sow-research.bank.internal/agent/api/v1/embed/grants",
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
    """An journey-portal deployment that fronts no cdd-sow-research Mode 5 installation has nothing
    to register.
    """
    assert _load(tmp_path, _valid_values()) is not None


def test_a_complete_mode5_registration_validates(tmp_path: Path) -> None:
    config = _load_with_mode5(tmp_path, {**_valid_values(), **_MODE5_VALUES})
    assert config.values["PORTAL_DOC1_BFF_CLIENT_ID"] == "hrz9-journey-portal-bff"


@pytest.mark.parametrize("omitted", sorted(_MODE5_VALUES))
def test_a_partial_mode5_registration_is_refused(tmp_path: Path, omitted: str) -> None:
    values = {**_valid_values(), **_MODE5_VALUES}
    values.pop(omitted)
    with pytest.raises(DeploymentConfigError, match="cdd-sow-research Mode 5 registration"):
        _load_with_mode5(tmp_path, values)


def test_a_mode5_registration_without_a_session_signing_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(DeploymentConfigError, match="PORTAL_SESSION_SIGNING_KEY"):
        _load_with_mode5(tmp_path, {**_valid_values(), **_MODE5_VALUES}, session_key="")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("PORTAL_PUBLIC_ORIGIN", "http://portal.bank.internal", "HTTPS"),
        ("PORTAL_PUBLIC_ORIGIN", "https://portal.bank.internal/", "trailing slash"),
        ("PORTAL_DOC1_GRANT_ENDPOINT", "cdd-sow-research.bank.internal/grants", "HTTPS"),
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
            "cdd-sow-research": {
                "label": "One",
                "ui_upstream": "https://cdd-sow-research-ui.example.com",
                "api_upstream": "https://cdd-sow-research-api.example.com",
                "canonical_mount_path": "/agent",
            },
            "credit-memo-drafting": {
                "label": "Two",
                "ui_upstream": "http://127.0.0.1:3102",
                "api_upstream": "http://127.0.0.1:8093",
            },
        },
        "journeys": {
            "rm": {
                "label": "RM",
                "blurb": "b",
                "apps": ["cdd-sow-research", "credit-memo-drafting"],
            },
            "ops": {"label": "Ops", "blurb": "b", "apps": ["credit-memo-drafting"]},
        },
    }

    catalog = JourneyCatalog.from_mapping(raw, only_apps=frozenset({"cdd-sow-research"}))
    assert set(catalog.apps) == {"cdd-sow-research"}
    # The rm journey keeps its deployed app...
    assert catalog.journeys["rm"].app_ids == ("cdd-sow-research",)
    # ...and the ops journey, whose every app belongs to another installation, is dropped
    # rather than shown as an empty dead end.
    assert "ops" not in catalog.journeys


def test_an_app_named_by_the_deployment_but_absent_from_config_is_an_error() -> None:
    """Silently dropping it would hide a typo AND a genuinely misconfigured upstream."""
    from journey_portal.domain.catalog import JourneyCatalog
    from journey_portal.domain.errors import JourneyConfigError

    raw = {
        "apps": {
            "cdd-sow-research": {
                "label": "One",
                "ui_upstream": "https://cdd-sow-research-ui.example.com",
                "api_upstream": "https://cdd-sow-research-api.example.com",
                "canonical_mount_path": "/agent",
            }
        },
        "journeys": {"rm": {"label": "RM", "blurb": "b", "apps": ["cdd-sow-research"]}},
    }

    with pytest.raises(JourneyConfigError, match="not present in the journeys config"):
        JourneyCatalog.from_mapping(raw, only_apps=frozenset({"cdd-sow-research", "doc9"}))


def test_a_deployment_with_no_serviceable_journey_is_an_error() -> None:
    from journey_portal.domain.catalog import JourneyCatalog
    from journey_portal.domain.errors import JourneyConfigError

    raw = {
        "apps": {
            "cdd-sow-research": {
                "label": "One",
                "ui_upstream": "https://cdd-sow-research-ui.example.com",
                "api_upstream": "https://cdd-sow-research-api.example.com",
                "canonical_mount_path": "/agent",
            },
            "credit-memo-drafting": {
                "label": "Two",
                "ui_upstream": "https://credit-memo-drafting-ui.example.com",
                "api_upstream": "https://credit-memo-drafting-api.example.com",
            },
        },
        "journeys": {"ops": {"label": "Ops", "blurb": "b", "apps": ["credit-memo-drafting"]}},
    }

    with pytest.raises(JourneyConfigError, match="no journey has any deployed app"):
        JourneyCatalog.from_mapping(raw, only_apps=frozenset({"cdd-sow-research"}))


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
