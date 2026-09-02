mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "000000000000"
    }
  }
  mock_data "google_logging_project_cmek_settings" {
    defaults = {
      service_account_id = "serviceAccount:logging@system.gserviceaccount.com"
    }
  }
  mock_resource "google_compute_backend_service" {
    defaults = {
      generated_id = "000000000001"
    }
    override_during = plan
  }
  mock_resource "google_kms_crypto_key" {
    defaults = {
      id = "projects/hrz9-test-00001/locations/asia-southeast1/keyRings/hrz9-test-portal/cryptoKeys/hrz9-test-portal"
    }
    override_during = plan
  }
}

variables {
  project_id                       = "hrz9-test-00001"
  name_prefix                      = "hrz9-test"
  region                           = "asia-southeast1"
  allowed_regions                  = ["asia-southeast1"]
  rm_domain                        = "rm.hrz9.example.test"
  ops_domain                       = "ops.hrz9.example.test"
  iap_oauth2_client_id             = "000000000000-test.apps.example.test"
  iap_oauth2_client_secret         = "synthetic-test-value"
  portal_audit_hmac_secret         = "hrz9-test-portal-audit-hmac"
  portal_audit_hmac_secret_version = "1"
  observability_url                = "https://observability.hrz9.example.test"
  observability_audience           = "https://observability-audience.hrz9.example.test"
  iap_jwt_audience                 = "/projects/000000000000/global/backendServices/1"
  iap_members                      = ["group:journey-users@example.test"]
  notification_channels            = ["projects/hrz9-test-00001/notificationChannels/1"]
  tenant_embed_policies = {
    hrz9-test-primary = {
      tenant          = "hrz9-test"
      hosts           = ["rm.hrz9.example.test", "ops.hrz9.example.test"]
      frame_ancestors = ["'self'", "https://host.hrz9.example.test"]
      cors_origins    = ["https://host.hrz9.example.test"]
    }
  }
  vpc_sc_access_policy_id = "123456789"
  # The contract's default posture builds the edge, so `complete_edge_and_private_services`
  # keeps asserting against a complete one. The stack's own default is false -- guarding the
  # edge must not silently shrink what this file covers.
  production_edge_enabled = true
  bff_image               = "registry.example.test/bff@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  rm_shell_image          = "registry.example.test/rm@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  ops_shell_image         = "registry.example.test/ops@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  rollback_images = {
    bff                            = "registry.example.test/bff@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    rm                             = "registry.example.test/rm@sha256:2222222222222222222222222222222222222222222222222222222222222222"
    ops                            = "registry.example.test/ops@sha256:3333333333333333333333333333333333333333333333333333333333333333"
    cdd-sow-research-ui            = "registry.example.test/cdd-sow-research-ui@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    cdd-sow-research-api           = "registry.example.test/cdd-sow-research-api@sha256:1111111111111111111111111111111111111111111111111111111111111111"
    credit-memo-drafting-ui        = "registry.example.test/credit-memo-drafting-ui@sha256:2222222222222222222222222222222222222222222222222222222222222222"
    credit-memo-drafting-api       = "registry.example.test/credit-memo-drafting-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
    cio-advisory-ui                = "registry.example.test/cio-advisory-ui@sha256:3333333333333333333333333333333333333333333333333333333333333333"
    cio-advisory-api               = "registry.example.test/cio-advisory-api@sha256:3333333333333333333333333333333333333333333333333333333333333333"
    trade-finance-checker-ui       = "registry.example.test/trade-finance-checker-ui@sha256:4444444444444444444444444444444444444444444444444444444444444444"
    trade-finance-checker-api      = "registry.example.test/trade-finance-checker-api@sha256:4444444444444444444444444444444444444444444444444444444444444444"
    loan-document-intelligence-ui  = "registry.example.test/loan-document-intelligence-ui@sha256:7777777777777777777777777777777777777777777777777777777777777777"
    loan-document-intelligence-api = "registry.example.test/loan-document-intelligence-api@sha256:7777777777777777777777777777777777777777777777777777777777777777"
    compliance-advisory-ui         = "registry.example.test/compliance-advisory-ui@sha256:5555555555555555555555555555555555555555555555555555555555555555"
    compliance-advisory-api        = "registry.example.test/compliance-advisory-api@sha256:5555555555555555555555555555555555555555555555555555555555555555"
    human-review-console-ui        = "registry.example.test/human-review-console-ui@sha256:6666666666666666666666666666666666666666666666666666666666666666"
    human-review-console-api       = "registry.example.test/human-review-console-api@sha256:6666666666666666666666666666666666666666666666666666666666666666"
  }
  embedded_apps = {
    cdd-sow-research = {
      ui_image           = "registry.example.test/cdd-sow-research-ui@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
      api_image          = "registry.example.test/cdd-sow-research-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
      ui_build_base_path = "/agent"
      ui_env             = { UI_ONLY = "ui" }
      ui_secret_env      = { UI_SECRET = "cdd-sow-research-ui-secret" }
      api_env            = { API_ONLY = "api", CDD_PROFILE = "gcp" }
      api_secret_env = {
        API_SECRET = "cdd-sow-research-api-secret"
      }
    }
    credit-memo-drafting = {
      ui_image           = "registry.example.test/credit-memo-drafting-ui@sha256:2222222222222222222222222222222222222222222222222222222222222222"
      api_image          = "registry.example.test/credit-memo-drafting-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
      ui_build_base_path = "/apps/credit-memo-drafting"
      api_env            = { CREDIT_MEMO_PROFILE = "gcp" }
    }
    cio-advisory = {
      ui_image           = "registry.example.test/cio-advisory-ui@sha256:3333333333333333333333333333333333333333333333333333333333333333"
      api_image          = "registry.example.test/cio-advisory-api@sha256:3333333333333333333333333333333333333333333333333333333333333333"
      ui_build_base_path = "/apps/cio-advisory"
      api_env            = { CIO_PROFILE = "gcp" }
    }
    trade-finance-checker = {
      ui_image           = "registry.example.test/trade-finance-checker-ui@sha256:4444444444444444444444444444444444444444444444444444444444444444"
      api_image          = "registry.example.test/trade-finance-checker-api@sha256:4444444444444444444444444444444444444444444444444444444444444444"
      ui_build_base_path = "/apps/trade-finance-checker"
      api_env            = { TRADE_FINANCE_PROFILE = "gcp" }
    }
    loan-document-intelligence = {
      ui_image           = "registry.example.test/loan-document-intelligence-ui@sha256:7777777777777777777777777777777777777777777777777777777777777777"
      api_image          = "registry.example.test/loan-document-intelligence-api@sha256:7777777777777777777777777777777777777777777777777777777777777777"
      ui_build_base_path = "/apps/loan-document-intelligence"
      api_env            = { LOAN_DOC_PROFILE = "gcp" }
    }
    compliance-advisory = {
      ui_image           = "registry.example.test/compliance-advisory-ui@sha256:5555555555555555555555555555555555555555555555555555555555555555"
      api_image          = "registry.example.test/compliance-advisory-api@sha256:5555555555555555555555555555555555555555555555555555555555555555"
      ui_build_base_path = "/apps/compliance-advisory"
      api_env            = { COMPLIANCE_PROFILE = "gcp" }
    }
    human-review-console = {
      ui_image           = "registry.example.test/human-review-console-ui@sha256:6666666666666666666666666666666666666666666666666666666666666666"
      api_image          = "registry.example.test/human-review-console-api@sha256:6666666666666666666666666666666666666666666666666666666666666666"
      ui_build_base_path = "/apps/human-review-console"
      api_env            = { REVIEW_PROFILE = "gcp" }
    }
  }
}

run "complete_edge_and_private_services" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service.portal.ingress == "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
    error_message = "The BFF must not expose a direct public bypass."
  }

  assert {
    condition     = google_compute_backend_service.portal.iap[0].enabled
    error_message = "The public BFF edge must be IAP protected."
  }

  assert {
    condition     = google_cloud_run_v2_service.embedded_api["cdd-sow-research"].ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY"
    error_message = "Embedded APIs must remain private to authenticated service calls."
  }

  assert {
    condition     = google_cloud_run_v2_service.portal.template[0].vpc_access[0].egress == "ALL_TRAFFIC"
    error_message = "The BFF must use Direct VPC egress for internal-only Cloud Run destinations."
  }

  assert {
    condition     = google_compute_subnetwork.portal.private_ip_google_access
    error_message = "The dedicated BFF subnet must enable Private Google Access."
  }

  assert {
    condition     = google_compute_subnetwork.portal.ip_cidr_range == "10.42.0.0/26"
    error_message = "Direct VPC egress needs at least the documented /26 address range."
  }

  assert {
    condition = (
      google_compute_router_nat.portal.source_subnetwork_ip_ranges_to_nat == "LIST_OF_SUBNETWORKS" &&
      one(google_compute_router_nat.portal.subnetwork).source_ip_ranges_to_nat == toset(["ALL_IP_RANGES"]) &&
      google_compute_router_nat.portal.log_config[0].enable
    )
    error_message = "The dedicated subnet needs logged Cloud NAT for IAP public-key retrieval."
  }

  assert {
    condition     = google_service_account.embedded_ui["cdd-sow-research"].account_id != google_service_account.embedded_api["cdd-sow-research"].account_id
    error_message = "Embedded UI and API surfaces must have distinct runtime identities."
  }

  assert {
    condition = (
      contains([for item in google_cloud_run_v2_service.embedded_ui["cdd-sow-research"].template[0].containers[0].env : item.name], "UI_ONLY") &&
      contains([for item in google_cloud_run_v2_service.embedded_ui["cdd-sow-research"].template[0].containers[0].env : item.name], "UI_SECRET") &&
      !contains([for item in google_cloud_run_v2_service.embedded_ui["cdd-sow-research"].template[0].containers[0].env : item.name], "API_ONLY") &&
      !contains([for item in google_cloud_run_v2_service.embedded_ui["cdd-sow-research"].template[0].containers[0].env : item.name], "API_SECRET")
    )
    error_message = "The UI runtime must receive only UI-scoped configuration and secrets."
  }

  assert {
    condition = (
      contains([for item in google_cloud_run_v2_service.embedded_api["cdd-sow-research"].template[0].containers[0].env : item.name], "API_ONLY") &&
      contains([for item in google_cloud_run_v2_service.embedded_api["cdd-sow-research"].template[0].containers[0].env : item.name], "API_SECRET") &&
      contains([for item in google_cloud_run_v2_service.embedded_api["cdd-sow-research"].template[0].containers[0].env : item.name], "CDD_IAP_AUDIENCE") &&
      !contains([for item in google_cloud_run_v2_service.embedded_api["cdd-sow-research"].template[0].containers[0].env : item.name], "UI_ONLY") &&
      !contains([for item in google_cloud_run_v2_service.embedded_api["cdd-sow-research"].template[0].containers[0].env : item.name], "UI_SECRET")
    )
    error_message = "The API runtime must receive only API-scoped configuration and secrets."
  }

  assert {
    condition = (
      length(google_secret_manager_secret_iam_member.embedded_ui_secret_access) == 1 &&
      length(google_secret_manager_secret_iam_member.embedded_api_secret_access) == 1 &&
      google_secret_manager_secret_iam_member.embedded_ui_secret_access["cdd-sow-research-UI_SECRET"].secret_id == "cdd-sow-research-ui-secret" &&
      google_secret_manager_secret_iam_member.embedded_api_secret_access["cdd-sow-research-API_SECRET"].secret_id == "cdd-sow-research-api-secret"
    )
    error_message = "Secret Manager grants must preserve UI/API identity isolation."
  }

  assert {
    condition     = length(google_iap_web_backend_service_iam_member.access) == 3
    error_message = "Each approved member must receive three backend-scoped IAP grants."
  }

  assert {
    condition     = output.computed_portal_iap_audience == var.iap_jwt_audience
    error_message = "The configured audience must match the provider-computed backend audience."
  }

  assert {
    condition     = output.image_digests["bff"] == var.bff_image
    error_message = "Deployment outputs must preserve the exact reviewed image digest."
  }

  assert {
    condition = (
      google_cloud_run_v2_service.portal.template[0].encryption_key == google_kms_crypto_key.portal.id &&
      google_logging_project_bucket_config.audit.cmek_settings[0].kms_key_name == google_kms_crypto_key.portal.id
    )
    error_message = "Cloud Run and the audit bucket must use the regional CMEK."
  }

  assert {
    condition     = google_logging_project_bucket_config.audit.retention_days == 180
    error_message = "Audit retention must default to six months."
  }

  assert {
    condition = (
      google_access_context_manager_service_perimeter.portal[0].use_explicit_dry_run_spec &&
      length(google_access_context_manager_service_perimeter.portal[0].status) == 0
    )
    error_message = "VPC-SC must start in explicit dry-run mode."
  }
}

run "reject_region_outside_allowlist" {
  command = plan
  variables {
    region = "australia-southeast1"
  }
  expect_failures = [var.region]
}

run "reject_empty_region_allowlist" {
  command = plan
  variables {
    allowed_regions = []
  }
  expect_failures = [var.allowed_regions]
}

# The region is a deploy-time input: a reviewed allowlist extension deploys, and every
# regional service follows the selected region rather than a literal.
run "accept_reviewed_second_region" {
  command = plan
  variables {
    region          = "australia-southeast1"
    allowed_regions = ["asia-southeast1", "australia-southeast1"]
  }

  assert {
    condition = (
      google_cloud_run_v2_service.portal.location == "australia-southeast1" &&
      google_cloud_run_v2_service.embedded_api["cdd-sow-research"].location == "australia-southeast1"
    )
    error_message = "Regional services must follow the selected deploy-time region."
  }
}

run "reject_audit_retention_under_six_months" {
  command = plan
  variables {
    audit_retention_days = 179
  }
  expect_failures = [var.audit_retention_days]
}

run "reject_kms_rotation_below_24_hours" {
  command = plan
  variables {
    cmek_rotation_period = "86399s"
  }
  expect_failures = [var.cmek_rotation_period]
}

run "reject_kms_rotation_above_cloud_kms_maximum" {
  command = plan
  variables {
    cmek_rotation_period = "3153600001s"
  }
  expect_failures = [var.cmek_rotation_period]
}

run "reject_api_secret_collision_with_managed_environment" {
  command = plan
  variables {
    embedded_apps = {
      cdd-sow-research = {
        ui_image           = "registry.example.test/cdd-sow-research-ui@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        api_image          = "registry.example.test/cdd-sow-research-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        ui_build_base_path = "/agent"
        api_env            = { CDD_PROFILE = "gcp" }
        api_secret_env     = { CDD_PROFILE = "cdd-sow-research-profile-secret" }
      }
      credit-memo-drafting = {
        ui_image           = "registry.example.test/credit-memo-drafting-ui@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        api_image          = "registry.example.test/credit-memo-drafting-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        ui_build_base_path = "/apps/credit-memo-drafting"
        api_env            = { CREDIT_MEMO_PROFILE = "gcp" }
      }
      cio-advisory = {
        ui_image           = "registry.example.test/cio-advisory-ui@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        api_image          = "registry.example.test/cio-advisory-api@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        ui_build_base_path = "/apps/cio-advisory"
        api_env            = { CIO_PROFILE = "gcp" }
      }
      trade-finance-checker = {
        ui_image           = "registry.example.test/trade-finance-checker-ui@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        api_image          = "registry.example.test/trade-finance-checker-api@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        ui_build_base_path = "/apps/trade-finance-checker"
        api_env            = { TRADE_FINANCE_PROFILE = "gcp" }
      }
      compliance-advisory = {
        ui_image           = "registry.example.test/compliance-advisory-ui@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        api_image          = "registry.example.test/compliance-advisory-api@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        ui_build_base_path = "/apps/compliance-advisory"
        api_env            = { COMPLIANCE_PROFILE = "gcp" }
      }
      human-review-console = {
        ui_image           = "registry.example.test/human-review-console-ui@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        api_image          = "registry.example.test/human-review-console-api@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        ui_build_base_path = "/apps/human-review-console"
        api_env            = { REVIEW_PROFILE = "gcp" }
      }
    }
  }
  expect_failures = [var.embedded_apps]
}

run "reject_api_env_using_another_apps_profile" {
  command = plan
  variables {
    embedded_apps = {
      cdd-sow-research = {
        ui_image           = "registry.example.test/cdd-sow-research-ui@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        api_image          = "registry.example.test/cdd-sow-research-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        ui_build_base_path = "/agent"
        api_env            = { CDD_PROFILE = "gcp", CIO_PROFILE = "gcp" }
      }
      credit-memo-drafting = {
        ui_image           = "registry.example.test/credit-memo-drafting-ui@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        api_image          = "registry.example.test/credit-memo-drafting-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        ui_build_base_path = "/apps/credit-memo-drafting"
        api_env            = { CREDIT_MEMO_PROFILE = "gcp" }
      }
      cio-advisory = {
        ui_image           = "registry.example.test/cio-advisory-ui@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        api_image          = "registry.example.test/cio-advisory-api@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        ui_build_base_path = "/apps/cio-advisory"
        api_env            = { CIO_PROFILE = "gcp" }
      }
      trade-finance-checker = {
        ui_image           = "registry.example.test/trade-finance-checker-ui@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        api_image          = "registry.example.test/trade-finance-checker-api@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        ui_build_base_path = "/apps/trade-finance-checker"
        api_env            = { TRADE_FINANCE_PROFILE = "gcp" }
      }
      compliance-advisory = {
        ui_image           = "registry.example.test/compliance-advisory-ui@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        api_image          = "registry.example.test/compliance-advisory-api@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        ui_build_base_path = "/apps/compliance-advisory"
        api_env            = { COMPLIANCE_PROFILE = "gcp" }
      }
      human-review-console = {
        ui_image           = "registry.example.test/human-review-console-ui@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        api_image          = "registry.example.test/human-review-console-api@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        ui_build_base_path = "/apps/human-review-console"
        api_env            = { REVIEW_PROFILE = "gcp" }
      }
    }
  }
  expect_failures = [var.embedded_apps]
}

run "reject_ui_env_using_cloud_run_managed_name" {
  command = plan
  variables {
    embedded_apps = {
      cdd-sow-research = {
        ui_image           = "registry.example.test/cdd-sow-research-ui@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        api_image          = "registry.example.test/cdd-sow-research-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        ui_build_base_path = "/agent"
        ui_env             = { PORT = "3000" }
        api_env            = { CDD_PROFILE = "gcp" }
      }
      credit-memo-drafting = {
        ui_image           = "registry.example.test/credit-memo-drafting-ui@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        api_image          = "registry.example.test/credit-memo-drafting-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        ui_build_base_path = "/apps/credit-memo-drafting"
        api_env            = { CREDIT_MEMO_PROFILE = "gcp" }
      }
      cio-advisory = {
        ui_image           = "registry.example.test/cio-advisory-ui@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        api_image          = "registry.example.test/cio-advisory-api@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        ui_build_base_path = "/apps/cio-advisory"
        api_env            = { CIO_PROFILE = "gcp" }
      }
      trade-finance-checker = {
        ui_image           = "registry.example.test/trade-finance-checker-ui@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        api_image          = "registry.example.test/trade-finance-checker-api@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        ui_build_base_path = "/apps/trade-finance-checker"
        api_env            = { TRADE_FINANCE_PROFILE = "gcp" }
      }
      compliance-advisory = {
        ui_image           = "registry.example.test/compliance-advisory-ui@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        api_image          = "registry.example.test/compliance-advisory-api@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        ui_build_base_path = "/apps/compliance-advisory"
        api_env            = { COMPLIANCE_PROFILE = "gcp" }
      }
      human-review-console = {
        ui_image           = "registry.example.test/human-review-console-ui@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        api_image          = "registry.example.test/human-review-console-api@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        ui_build_base_path = "/apps/human-review-console"
        api_env            = { REVIEW_PROFILE = "gcp" }
      }
    }
  }
  expect_failures = [var.embedded_apps]
}

run "reject_plain_and_secret_source_collision" {
  command = plan
  variables {
    embedded_apps = {
      cdd-sow-research = {
        ui_image           = "registry.example.test/cdd-sow-research-ui@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
        api_image          = "registry.example.test/cdd-sow-research-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        ui_build_base_path = "/agent"
        ui_env             = { UI_SETTING = "plain" }
        ui_secret_env      = { UI_SETTING = "cdd-sow-research-ui-secret" }
        api_env            = { CDD_PROFILE = "gcp" }
      }
      credit-memo-drafting = {
        ui_image           = "registry.example.test/credit-memo-drafting-ui@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        api_image          = "registry.example.test/credit-memo-drafting-api@sha256:2222222222222222222222222222222222222222222222222222222222222222"
        ui_build_base_path = "/apps/credit-memo-drafting"
        api_env            = { CREDIT_MEMO_PROFILE = "gcp" }
      }
      cio-advisory = {
        ui_image           = "registry.example.test/cio-advisory-ui@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        api_image          = "registry.example.test/cio-advisory-api@sha256:3333333333333333333333333333333333333333333333333333333333333333"
        ui_build_base_path = "/apps/cio-advisory"
        api_env            = { CIO_PROFILE = "gcp" }
      }
      trade-finance-checker = {
        ui_image           = "registry.example.test/trade-finance-checker-ui@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        api_image          = "registry.example.test/trade-finance-checker-api@sha256:4444444444444444444444444444444444444444444444444444444444444444"
        ui_build_base_path = "/apps/trade-finance-checker"
        api_env            = { TRADE_FINANCE_PROFILE = "gcp" }
      }
      compliance-advisory = {
        ui_image           = "registry.example.test/compliance-advisory-ui@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        api_image          = "registry.example.test/compliance-advisory-api@sha256:5555555555555555555555555555555555555555555555555555555555555555"
        ui_build_base_path = "/apps/compliance-advisory"
        api_env            = { COMPLIANCE_PROFILE = "gcp" }
      }
      human-review-console = {
        ui_image           = "registry.example.test/human-review-console-ui@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        api_image          = "registry.example.test/human-review-console-api@sha256:6666666666666666666666666666666666666666666666666666666666666666"
        ui_build_base_path = "/apps/human-review-console"
        api_env            = { REVIEW_PROFILE = "gcp" }
      }
    }
  }
  expect_failures = [var.embedded_apps]
}

run "safe_stage_one_bootstrap" {
  command = plan
  variables {
    iap_jwt_audience = ""
    iap_members      = []
  }

  assert {
    condition     = length(google_iap_web_backend_service_iam_member.access) == 0
    error_message = "Bootstrap must create no user access grants."
  }

  assert {
    condition     = output.computed_portal_iap_audience == "/projects/000000000000/global/backendServices/1"
    error_message = "Stage one must expose the exact audience needed by stage two."
  }
}

run "reject_access_with_mismatched_audience" {
  command = plan
  variables {
    iap_jwt_audience = "/projects/000000000000/global/backendServices/999999999999"
  }
  expect_failures = [terraform_data.iap_access_contract]
}

run "reject_vpc_sc_enforcement_until_egress_is_restricted" {
  command = plan
  variables {
    vpc_sc_enforced = true
  }
  expect_failures = [terraform_data.vpc_sc_contract]
}

run "reject_missing_notification_channels" {
  command = plan
  variables {
    notification_channels = []
  }
  expect_failures = [var.notification_channels]
}

run "reject_duplicate_notification_channels" {
  command = plan
  variables {
    notification_channels = [
      "projects/hrz9-test-00001/notificationChannels/1",
      "projects/hrz9-test-00001/notificationChannels/1",
    ]
  }
  expect_failures = [var.notification_channels]
}

run "reject_cross_project_notification_channel" {
  command = plan
  variables {
    notification_channels = ["projects/other-test-00001/notificationChannels/1"]
  }
  expect_failures = [terraform_data.deployment_contract]
}

run "reject_consecutive_dot_tenant_policy_origin" {
  command = plan
  variables {
    tenant_embed_policies = {
      invalid = {
        tenant          = "hrz9-test"
        hosts           = ["rm.hrz9.example.test", "ops.hrz9.example.test"]
        frame_ancestors = ["'self'", "https://a..example.test"]
        cors_origins    = []
      }
    }
  }
  expect_failures = [var.tenant_embed_policies]
}

run "reject_explicit_port_tenant_policy_origin" {
  command = plan
  variables {
    tenant_embed_policies = {
      invalid = {
        tenant          = "hrz9-test"
        hosts           = ["rm.hrz9.example.test", "ops.hrz9.example.test"]
        frame_ancestors = ["'self'"]
        cors_origins    = ["https://host.example.test:443"]
      }
    }
  }
  expect_failures = [var.tenant_embed_policies]
}

run "reject_uppercase_tenant_policy_origin" {
  command = plan
  variables {
    tenant_embed_policies = {
      invalid = {
        tenant          = "hrz9-test"
        hosts           = ["rm.hrz9.example.test", "ops.hrz9.example.test"]
        frame_ancestors = ["'self'"]
        cors_origins    = ["https://HOST.example.test"]
      }
    }
  }
  expect_failures = [var.tenant_embed_policies]
}

run "reject_non_https_observability_url" {
  command = plan
  variables {
    observability_url = "http://observability.hrz9.example.test"
  }
  expect_failures = [var.observability_url]
}

run "reject_observability_audience_path" {
  command = plan
  variables {
    observability_audience = "https://observability.hrz9.example.test/path"
  }
  expect_failures = [var.observability_audience]
}


# The guard is the whole point of the variable, so it is asserted in both directions: the run
# above proves the edge is built when the deployment asks for it, and this one proves nothing
# is left behind when it declines. Counting the resources rather than reading the flag is what
# makes this a test -- the reference deployment released a global address by hand on 2026-09-02
# after the LB in front of it was deleted, and an unattached address bills MORE than an attached
# one, so a guard that half-applies costs money silently.
run "declined_edge_builds_no_billable_edge" {
  command = plan
  variables {
    production_edge_enabled = false
  }

  assert {
    condition = (
      length(google_compute_global_forwarding_rule.portal_https) == 0 &&
      length(google_compute_target_https_proxy.portal) == 0 &&
      length(google_compute_url_map.portal) == 0 &&
      length(google_compute_managed_ssl_certificate.portal) == 0 &&
      length(google_compute_global_address.portal) == 0
    )
    error_message = "A declined edge must leave no forwarding rule, proxy, url map, certificate or address."
  }

  assert {
    condition = (
      google_compute_backend_service.portal.iap[0].enabled &&
      google_compute_backend_service.rm_shell.iap[0].enabled &&
      google_compute_backend_service.ops_shell.iap[0].enabled
    )
    error_message = "Declining the edge must keep the backend services: they are free without a forwarding rule and are what a rebuild reuses."
  }
}
