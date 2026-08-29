from pathlib import Path

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


def test_embedded_apps_receive_the_edge_iap_audience() -> None:
    cloud_run = _source("cloud_run.tf")

    for env_name in (
        "CDD_IAP_AUDIENCE",
        "CREDIT_MEMO_IAP_AUDIENCE",
        "CIO_IAP_AUDIENCE",
        "TRADE_FINANCE_IAP_AUDIENCE",
        "COMPLIANCE_IAP_AUDIENCE",
        "REVIEW_IAP_AUDIENCE",
    ):
        assert env_name in cloud_run


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


def test_terraform_requires_all_apps_managed_profiles_and_alert_delivery() -> None:
    variables = _source("variables.tf")
    main = _source("main.tf")

    # A deployment names the SUBSET of journeys it serves; requiring all seven on every apply
    # coupled seven independently-released repositories into one atomic deployment. What must
    # still hold is that the set is non-empty and that every id in it is one the portal knows.
    assert "length(var.embedded_apps) > 0" in variables
    for app_id in ("cdd-sow-research", "credit-memo-drafting", "cio-advisory", "trade-finance-checker", "loan-document-intelligence", "compliance-advisory", "human-review-console"):
        assert f'"{app_id}"' in variables
    for profile_env in (
        "CDD_PROFILE",
        "CREDIT_MEMO_PROFILE",
        "CIO_PROFILE",
        "TRADE_FINANCE_PROFILE",
        "LOAN_DOC_PROFILE",
        "COMPLIANCE_PROFILE",
        "REVIEW_PROFILE",
    ):
        assert profile_env in variables
    assert "length(var.notification_channels) > 0" in variables
    assert "every embedded UI/API" in main
    assert 'id == "cdd-sow-research" ? "/agent" : "/apps/${id}"' in variables
    assert "setintersection" in variables
    assert "K_SERVICE" in variables
    assert "CDD_IAP_AUDIENCE" in variables
    assert "UI/API plain and secret env sources must not overlap" in variables
    assert "setsubtract" in variables


def test_kms_rotation_matches_cloud_kms_bounds() -> None:
    variables = _source("variables.tf")

    assert ">= 86400" in variables
    assert "<= 3153600000" in variables
    assert r"\\.[0-9]{1,9}" in variables
