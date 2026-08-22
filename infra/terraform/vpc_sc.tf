resource "google_access_context_manager_service_perimeter" "portal" {
  count  = var.vpc_sc_access_policy_id == "" ? 0 : 1
  parent = "accessPolicies/${var.vpc_sc_access_policy_id}"
  name   = "accessPolicies/${var.vpc_sc_access_policy_id}/servicePerimeters/${replace(var.name_prefix, "-", "_")}"
  title  = "${var.name_prefix} Hrz9 portal"

  use_explicit_dry_run_spec = true
  spec {
    resources           = ["projects/${data.google_project.current.number}"]
    restricted_services = var.vpc_sc_restricted_services
  }

  dynamic "status" {
    for_each = var.vpc_sc_enforced ? [true] : []
    content {
      resources           = ["projects/${data.google_project.current.number}"]
      restricted_services = var.vpc_sc_restricted_services
    }
  }

  depends_on = [
    google_project_service.services["accesscontextmanager.googleapis.com"],
    terraform_data.vpc_sc_contract,
  ]
}
