data "google_project" "current" {
  project_id = var.project_id
}

locals {
  required_services = toset([
    "accesscontextmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "dns.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "orgpolicy.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  iap_service_agent = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-iap.iam.gserviceaccount.com"
  expected_rollback_components = concat(
    ["bff", "rm", "ops"],
    [for id in keys(var.embedded_apps) : "${id}-ui"],
    [for id in keys(var.embedded_apps) : "${id}-api"],
  )
  tenant_policy_hosts = flatten([
    for policy in values(var.tenant_embed_policies) : tolist(policy.hosts)
  ])
  tenant_embed_policies_json = jsonencode({
    for policy_id, policy in var.tenant_embed_policies : policy_id => {
      tenant          = policy.tenant
      hosts           = sort(tolist(policy.hosts))
      frame_ancestors = sort(tolist(policy.frame_ancestors))
      cors_origins    = sort(tolist(policy.cors_origins))
    }
  })
}

resource "google_project_service" "services" {
  for_each           = local.required_services
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "terraform_data" "deployment_contract" {
  input = {
    region                = var.region
    allowed_regions       = var.allowed_regions
    frame_ancestors       = var.frame_ancestors
    cors_origins          = var.cors_origins
    rollback_images       = var.rollback_images
    notification_channels = var.notification_channels
  }
  lifecycle {
    # Residency (region in allowed_regions) is owned by the var.region validation in
    # variables.tf: variable validation runs before preconditions, so the same condition
    # here would be unreachable. region and allowed_regions stay in the recorded contract
    # input above so the reviewed pair is visible in the plan.
    precondition {
      condition     = var.rm_domain != var.ops_domain
      error_message = "RM and Ops need distinct hostnames so each shell can own its root path."
    }
    precondition {
      condition = (
        length(local.tenant_policy_hosts) == length(distinct(local.tenant_policy_hosts)) &&
        length(setsubtract(toset(local.tenant_policy_hosts), toset([var.rm_domain, var.ops_domain]))) == 0 &&
        length(setsubtract(toset([var.rm_domain, var.ops_domain]), toset(local.tenant_policy_hosts))) == 0
      )
      error_message = "Each routed RM/Ops hostname must resolve to exactly one tenant embed policy."
    }
    precondition {
      condition = length(var.rollback_images) == length(local.expected_rollback_components) && alltrue([
        for component in local.expected_rollback_components :
        contains(keys(var.rollback_images), component)
      ])
      error_message = "rollback_images must cover the BFF, both shells, and every embedded UI/API."
    }
    precondition {
      condition = alltrue([
        for channel in var.notification_channels :
        startswith(channel, "projects/${var.project_id}/notificationChannels/")
      ])
      error_message = "notification_channels must belong to project_id."
    }
  }
}

resource "terraform_data" "vpc_sc_contract" {
  input = {
    access_policy_id = var.vpc_sc_access_policy_id
    enforced         = var.vpc_sc_enforced
  }
  lifecycle {
    precondition {
      condition     = !var.vpc_sc_enforced
      error_message = "VPC-SC enforcement is fail-disabled until restricted egress replaces unrestricted Cloud NAT."
    }
  }
}

resource "google_service_account" "portal" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-portal"
  display_name = "Hrz9 portal BFF runtime"
}

resource "google_service_account" "rm_shell" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-rm"
  display_name = "Hrz9 RM shell runtime"
}

resource "google_service_account" "ops_shell" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-ops"
  display_name = "Hrz9 Ops shell runtime"
}

resource "google_service_account" "embedded_ui" {
  for_each     = var.embedded_apps
  project      = var.project_id
  account_id   = "${substr(var.name_prefix, 0, 14)}-u-${substr(each.key, 0, 6)}-${substr(sha256(each.key), 0, 6)}"
  display_name = "Hrz9 ${each.key} embedded UI runtime"
}

resource "google_service_account" "embedded_api" {
  for_each     = var.embedded_apps
  project      = var.project_id
  account_id   = "${substr(var.name_prefix, 0, 14)}-a-${substr(each.key, 0, 6)}-${substr(sha256(each.key), 0, 6)}"
  display_name = "Hrz9 ${each.key} embedded API runtime"
}
