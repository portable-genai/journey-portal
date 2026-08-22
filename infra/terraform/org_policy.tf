resource "google_project_organization_policy" "resource_locations" {
  count      = var.apply_org_policies ? 1 : 0
  project    = var.project_id
  constraint = "constraints/gcp.resourceLocations"
  list_policy {
    allow {
      values = [for region in var.allowed_regions : "in:${region}-locations"]
    }
  }
  depends_on = [google_project_service.services]
}

resource "google_project_organization_policy" "no_sa_keys" {
  count      = var.apply_org_policies ? 1 : 0
  project    = var.project_id
  constraint = "constraints/iam.disableServiceAccountKeyCreation"
  boolean_policy {
    enforced = true
  }
  depends_on = [google_project_service.services]
}

resource "google_project_organization_policy" "no_sa_key_upload" {
  count      = var.apply_org_policies ? 1 : 0
  project    = var.project_id
  constraint = "constraints/iam.disableServiceAccountKeyUpload"
  boolean_policy {
    enforced = true
  }
  depends_on = [google_project_service.services]
}
