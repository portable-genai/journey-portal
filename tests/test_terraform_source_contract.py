import importlib.util
import re
import sys
from pathlib import Path

from journey_portal import deployment_config
from journey_portal.config import load_journeys_mapping

TERRAFORM = Path("infra/terraform")


def _source(name: str) -> str:
    return (TERRAFORM / name).read_text(encoding="utf-8")


def test_iap_access_is_backend_scoped_only() -> None:
    source = _source("iap.tf")

    assert 'resource "google_iap_web_backend_service_iam_member" "access"' in source
    assert 'resource "google_iap_web_iam_member"' not in source


def test_ui_and_api_secrets_bind_only_to_their_surface_identity() -> None:
    source = _source("iap.tf")

    assert "google_service_account.embedded_ui[each.value.app_id].email" in source
    assert "google_service_account.embedded_api[each.value.app_id].email" in source
    assert "google_service_account.embedded[each.value.app_id].email" not in source


def test_internal_only_upstreams_are_paired_with_direct_vpc_egress() -> None:
    source = _source("cloud_run.tf")
    network = _source("network.tf")

    assert source.count('ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"') == 2
    assert 'egress = "ALL_TRAFFIC"' in source
    assert "network_interfaces {" in source
    assert "private_ip_google_access = true" in network
    assert "depends_on              = [google_project_service.services]" in network
    assert 'resource "google_compute_router_nat" "portal"' in network
    assert 'source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"' in network
    # NAT flow logging is on, and what it captures is a reviewed variable rather than a
    # literal. This used to assert `filter = "ALL"` in place, which stopped being the
    # durable statement the moment the volume became something a deployment could lower:
    # the invariant is that logging is enabled and that declining full capture is a choice
    # someone makes, not that this file spells one value.
    assert "enable = true" in network
    assert "filter = var.nat_log_filter" in network
    variables = _source("variables.tf")
    assert 'variable "nat_log_filter"' in variables
    assert 'default     = "ALL"' in variables


def test_cmek_vpc_sc_and_retention_controls_are_code_enforced() -> None:
    cloud_run = _source("cloud_run.tf")
    audit = _source("audit.tf")
    kms = _source("kms.tf")
    perimeter = _source("vpc_sc.tf")
    variables = _source("variables.tf")

    assert cloud_run.count("encryption_key") == 5
    assert "cmek_settings {" in audit
    assert "prevent_destroy = true" in kms
    assert "use_explicit_dry_run_spec = true" in perimeter
    assert "default     = 180" in variables
    assert "var.audit_retention_days >= 180" in variables
    # The region is a deploy-time input validated against the residency allowlist, NOT a
    # literal pin. The DEFAULTS are a single region, so an unset deploy cannot spread across
    # jurisdictions; another region needs both variables set, which is the review. That single
    # region follows the portfolio decision (asia-southeast1 since its 2026-08-27 revision,
    # which returned it after the deferred availability check was run), and the two defaults
    # must agree or an unset deploy fails its own validation. allowed_regions carries
    # us-central1 as well for as long as the reference deployment still runs there.
    assert "contains(var.allowed_regions, var.region)" in variables
    assert "length(var.allowed_regions) > 0" in variables
    assert 'var.region == "us-central1"' not in variables
    assert 'var.allowed_regions == toset(["us-central1"])' not in variables
    assert 'default     = "asia-southeast1"' in variables
    assert 'default     = ["asia-southeast1", "us-central1"]' in variables


def test_portal_revision_waits_for_audit_permissions() -> None:
    cloud_run = _source("cloud_run.tf")
    portal_block = cloud_run.split('resource "google_cloud_run_v2_service" "rm_shell"')[0]

    assert "google_project_iam_member.portal_log_writer" in portal_block
    assert "google_secret_manager_secret_iam_member.portal_audit_hmac_access" in portal_block


def test_tenant_policy_registry_reaches_bff_and_both_shells() -> None:
    cloud_run = _source("cloud_run.tf")
    main = _source("main.tf")
    variables = _source("variables.tf")

    assert cloud_run.count('name  = "TENANT_EMBED_POLICIES_JSON"') == 2
    assert cloud_run.count('name  = "PORTAL_TENANT_EMBED_POLICIES_JSON"') == 1
    assert "tenant_embed_policies_json = jsonencode" in main
    assert "Each routed RM/Ops hostname must resolve to exactly one" in main
    assert 'variable "tenant_embed_policies"' in variables


def test_platform_profile_routes_portal_access_evidence_to_hrz5() -> None:
    cloud_run = _source("cloud_run.tf")
    variables = _source("variables.tf")

    assert 'name  = "PORTAL_PROFILE"' in cloud_run
    assert 'value = "platform"' in cloud_run
    assert 'name  = "PORTAL_OBSERVABILITY_URL"' in cloud_run
    assert 'name  = "PORTAL_OBSERVABILITY_AUDIENCE"' in cloud_run
    assert 'variable "observability_url"' in variables
    assert 'variable "observability_audience"' in variables


def test_terraform_requires_managed_profiles_and_alert_delivery() -> None:
    variables = _source("variables.tf")
    main = _source("main.tf")
    embedded = _source("embedded_apps.tf")

    # A deployment names the SUBSET of journeys it serves; what must still hold is that the set
    # is non-empty and that every id in it is one this stack can deploy.
    assert "length(var.embedded_apps) > 0" in variables
    assert "length(var.notification_channels) > 0" in variables
    assert "every embedded UI/API" in main
    assert 'id == "cdd-sow-research" ? "/agent" : "/apps/${id}"' in variables
    assert "setintersection" in variables
    assert "UI/API plain and secret env sources must not overlap" in variables
    assert '"K_SERVICE"' in embedded
    assert 'managed_embedded_profiles   = ["gcp", "platform"]' in embedded


_MANAGED_ENV_ENTRY = re.compile(
    r'\s*([a-z0-9-]+)\s*=\s*\{\s*profile\s*=\s*"([A-Z0-9_]+)"\s*,'
    r'\s*iap_audience\s*=\s*"([A-Z0-9_]+)"\s*\}\s*'
)


def _terraform_managed_env() -> dict[str, tuple[str, str]]:
    """Parse ``local.embedded_app_managed_env``, refusing any line it cannot read.

    Every non-blank line of the block must be an entry in the one reviewed shape, so an entry
    written differently fails here instead of silently dropping out of the comparison.
    """
    source = _source("embedded_apps.tf")
    opening = "  embedded_app_managed_env = {\n"
    assert source.count(opening) == 1
    body = source.split(opening, 1)[1].split("\n  }\n", 1)[0]
    entries: dict[str, tuple[str, str]] = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        match = _MANAGED_ENV_ENTRY.fullmatch(line)
        assert match, f"unreadable embedded_app_managed_env line: {line!r}"
        app_id, profile, audience = match.groups()
        assert app_id not in entries, f"{app_id} is mapped twice"
        entries[app_id] = (profile, audience)
    assert entries
    return entries


def test_terraform_and_the_renderer_can_deploy_the_same_apps() -> None:
    """The deployable set is one map in each language, and the two maps are equal.

    Terraform's allowed-id list once named sixteen apps while its profile map named seven, so
    nine allowed apps, marketing-compliance-gate among them, failed the profile check on every
    plan. The renderer carried a third copy that named yet another set.
    """
    terraform = _terraform_managed_env()

    assert terraform == deployment_config._MANAGED_ENV_BY_APP
    assert frozenset(terraform) == deployment_config._KNOWN_JOURNEY_APPS
    assert "marketing-compliance-gate" in terraform


def test_the_deployable_set_and_reserved_names_derive_from_the_one_map() -> None:
    embedded = _source("embedded_apps.tf")
    cloud_run = _source("cloud_run.tf")

    assert "deployable_embedded_app_ids = sort(keys(local.embedded_app_managed_env))" in embedded
    assert "[for app in values(local.embedded_app_managed_env) : app.profile]" in embedded
    assert "[for app in values(local.embedded_app_managed_env) : app.iap_audience]" in embedded
    assert 'lookup(app.api_env, local.embedded_app_managed_env[id].profile, "")' in embedded
    assert "local.embedded_app_managed_env[each.key].iap_audience" in cloud_run
    assert cloud_run.count("terraform_data.embedded_app_contract") == 2

    # No second copy may grow back. Outside embedded_apps.tf no .tf file names a managed variable,
    # or any catalog app id other than cdd-sow-research, whose /agent mount rule is the one
    # id-specific line the variable validation keeps.
    managed_names = {name for pair in _terraform_managed_env().values() for name in pair}
    catalog_ids = set(load_journeys_mapping(Path("config/journeys.yaml"))["apps"])
    assert catalog_ids >= set(_terraform_managed_env())
    for tf_file in sorted(TERRAFORM.glob("*.tf")):
        if tf_file.name == "embedded_apps.tf":
            continue
        text = tf_file.read_text(encoding="utf-8")
        for name in managed_names:
            assert not re.search(rf"(?<![A-Z0-9_]){name}(?![A-Z0-9_])", text), (
                f"{tf_file.name} names {name}; derive it from local.embedded_app_managed_env"
            )
        for app_id in catalog_ids - {"cdd-sow-research"}:
            assert not re.search(rf"(?<![a-z0-9-]){re.escape(app_id)}(?![a-z0-9-])", text), (
                f"{tf_file.name} names {app_id}; derive it from local.embedded_app_managed_env"
            )


def test_managed_variable_names_match_what_each_app_reads() -> None:
    """Each profile is the one the local launcher tells that app, and each audience follows it.

    The launcher's map is exercised by every local run of the journeys, so a profile name that
    disagrees with it is a name the app does not read. The fleet names an app's IAP audience
    ``<PREFIX>_IAP_AUDIENCE`` beside ``<PREFIX>_PROFILE``, which is the only check the audience
    name gets short of a deployment.
    """
    path = Path("scripts/run_journeys.py").resolve()
    spec = importlib.util.spec_from_file_location("run_journeys_contract", path)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = launcher
    spec.loader.exec_module(launcher)

    for app_id, (profile, audience) in _terraform_managed_env().items():
        assert launcher._APP_PROFILE_ENVS[app_id] == profile
        assert profile.endswith("_PROFILE")
        assert audience == profile.removesuffix("_PROFILE") + "_IAP_AUDIENCE"


def test_kms_rotation_matches_cloud_kms_bounds() -> None:
    variables = _source("variables.tf")

    assert ">= 86400" in variables
    assert "<= 3153600000" in variables
    assert r"\\.[0-9]{1,9}" in variables
