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

    # Four resources, not five: the two hand-written shells became one `for_each` over
    # var.shells, so the fifth occurrence went with the duplicated block and not with a service.
    # Every Cloud Run service in the file still declares the key.
    assert cloud_run.count("encryption_key") == 4
    assert cloud_run.count("encryption_key") == cloud_run.count(
        'resource "google_cloud_run_v2_service"'
    )
    assert "cmek_settings {" in audit
    assert "prevent_destroy = true" in kms
    assert "use_explicit_dry_run_spec = true" in perimeter
    assert "default     = 30" in variables
    assert "var.audit_retention_days >= 30" in variables
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
    # Split on the resource that FOLLOWS the portal, and assert the split happened: `str.split`
    # returns the whole string when its separator is absent, so a renamed following resource
    # would have left this test passing over the entire file and asserting nothing about order.
    separator = 'resource "google_cloud_run_v2_service" "shell"'
    assert cloud_run.count(separator) == 1
    portal_block = cloud_run.split(separator)[0]

    assert "google_project_iam_member.portal_log_writer" in portal_block
    assert "google_secret_manager_secret_iam_member.portal_audit_hmac_access" in portal_block


def test_tenant_policy_registry_reaches_bff_and_every_shell() -> None:
    cloud_run = _source("cloud_run.tf")
    main = _source("main.tf")
    variables = _source("variables.tf")

    # Once, not twice: the shells are one `for_each` resource now, so one env block serves every
    # shell a deployment names. Two occurrences would mean a second shell resource had grown back.
    assert cloud_run.count('name  = "TENANT_EMBED_POLICIES_JSON"') == 1
    assert cloud_run.count('name  = "PORTAL_TENANT_EMBED_POLICIES_JSON"') == 1
    assert "tenant_embed_policies_json = jsonencode" in main
    assert "Each routed shell hostname must resolve to exactly one" in main
    # The registry is held to the shell hostnames themselves, so a new host cannot be routed
    # without being registered and cannot be registered without being routed.
    assert "toset(local.shell_domains)" in main
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


def test_the_shell_set_is_one_list_every_persona_host_derives_from() -> None:
    """A fourth persona must be an entry in `var.shells`, never another hand-written pair.

    Until 2026-09-12 each shell was written out twice -- `rm_shell` and `ops_shell` -- across
    five resources, an IAP backend map, a url map, a certificate keeper, a DNS set, four outputs
    and a rollback list. Thirteen places, so the Marketing journey the catalog had composed all
    along had no host any deployment could publish it on. The guard below is that no .tf file
    outside shells.tf names a journey at all: the enumeration has exactly one home.
    """
    shells = _source("shells.tf")
    main = _source("main.tf")
    load_balancer = _source("load_balancer.tf")
    outputs = _source("outputs.tf")
    iap = _source("iap.tf")
    variables = _source("variables.tf")

    # The list, the map derived from it, and the catalog it is checked against.
    assert 'variable "shells"' in variables
    assert "shells_by_journey = { for shell in var.shells" in shells
    catalog_read = 'journey_catalog = yamldecode(file("${path.module}/../../config/journeys.yaml"))'
    assert catalog_read in shells
    assert 'resource "terraform_data" "shell_contract"' in shells

    # Every per-shell resource is one for_each over that map, and the url map and certificate
    # read the ORDERED list rather than the map, because their order is load-bearing.
    per_shell_resources = {
        "main.tf": ['resource "google_service_account" "shell"'],
        "cloud_run.tf": ['resource "google_cloud_run_v2_service" "shell"'],
        "load_balancer.tf": [
            'resource "google_compute_region_network_endpoint_group" "shell"',
            'resource "google_compute_backend_service" "shell"',
        ],
        "iap.tf": ['resource "google_cloud_run_v2_service_iam_member" "iap_shell_invoker"'],
    }
    for file_name, resources in per_shell_resources.items():
        source = _source(file_name)
        for resource in resources:
            assert source.count(resource) == 1, f"{file_name} must declare {resource} once"
            block = source.split(resource, 1)[1].split("\n}\n", 1)[0]
            assert re.search(r"for_each\s*=\s*local\.shells\b", block), (
                f"{resource} must be one for_each over local.shells"
            )
    assert 'domains = join("|", local.shell_domains)' in load_balancer
    assert "domains = local.shell_domains" in load_balancer
    assert "default_service = google_compute_backend_service.shell[local.primary_shell].id" in (
        load_balancer
    )
    assert "for_each = var.shells" in load_balancer
    assert "local.shell_journeys" in main
    assert "for journey, backend in google_compute_backend_service.shell" in iap
    assert "for journey, shell in local.shells" in outputs

    # No journey is named outside shells.tf. The `moved` blocks there carry `rm` and `ops`
    # because a state address is a fact about the existing deployment, not a new enumeration.
    catalog_journeys = set(load_journeys_mapping(Path("config/journeys.yaml"))["journeys"])
    assert {"rm", "ops", "mkt"} <= catalog_journeys
    for tf_file in sorted(TERRAFORM.glob("*.tf")):
        if tf_file.name == "shells.tf":
            continue
        text = tf_file.read_text(encoding="utf-8")
        for journey in catalog_journeys:
            word = rf"(?<![A-Za-z0-9_-]){re.escape(journey)}(?![A-Za-z0-9_-])"
            assert not re.search(word, text), (
                f"{tf_file.name} names the {journey} journey; derive it from var.shells"
            )
        for retired in ("rm_shell", "ops_shell", "rm_domain", "ops_domain"):
            assert retired not in text, f"{tf_file.name} still names {retired}"


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
