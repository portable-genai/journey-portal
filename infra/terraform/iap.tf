resource "google_cloud_run_v2_service_iam_member" "iap_portal_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.portal.name
  role     = "roles/run.invoker"
  member   = local.iap_service_agent
}

resource "google_cloud_run_v2_service_iam_member" "iap_rm_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.rm_shell.name
  role     = "roles/run.invoker"
  member   = local.iap_service_agent
}

resource "google_cloud_run_v2_service_iam_member" "iap_ops_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.ops_shell.name
  role     = "roles/run.invoker"
  member   = local.iap_service_agent
}

resource "google_cloud_run_v2_service_iam_member" "portal_ui_invoker" {
  for_each = var.embedded_apps
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.embedded_ui[each.key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.portal.email}"
}

resource "google_cloud_run_v2_service_iam_member" "portal_api_invoker" {
  for_each = var.embedded_apps
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.embedded_api[each.key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.portal.email}"
}

resource "google_secret_manager_secret_iam_member" "embedded_ui_secret_access" {
  for_each = merge([
    for app_id, app in var.embedded_apps : {
      for env_name, secret_name in app.ui_secret_env :
      "${app_id}-${env_name}" => { app_id = app_id, secret_name = secret_name }
    }
  ]...)
  project   = var.project_id
  secret_id = each.value.secret_name
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.embedded_ui[each.value.app_id].email}"
}

resource "google_secret_manager_secret_iam_member" "embedded_api_secret_access" {
  for_each = merge([
    for app_id, app in var.embedded_apps : {
      for env_name, secret_name in app.api_secret_env :
      "${app_id}-${env_name}" => { app_id = app_id, secret_name = secret_name }
    }
  ]...)
  project   = var.project_id
  secret_id = each.value.secret_name
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.embedded_api[each.value.app_id].email}"
}

resource "google_secret_manager_secret_iam_member" "portal_audit_hmac_access" {
  project   = var.project_id
  secret_id = var.portal_audit_hmac_secret
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.portal.email}"
}

resource "google_project_iam_member" "portal_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.portal.email}"
}

# The runtime baseline for every EMBEDDED app's identity.
#
# This stack creates a service account per embedded app, so this stack owes them the roles any
# service needs simply to run observably: write its own logs, and export its own spans. The
# embedded app's OWN stack cannot grant these, because it has never heard of an account the
# portal invented. Missing, the app runs and then fails on its first traced request with
# "Permission 'cloudtrace.traces.patch' denied" -- a message about tracing, on a request that was
# building a dossier.
#
# Deliberately only the baseline. Anything an app needs for its own DATA (a bucket, a dataset, a
# database) stays with that app's stack, which is where the resource and its access list live;
# this stack would otherwise be granting itself access to data it does not own.
locals {
  embedded_runtime_members = merge(
    {
      for app_id, sa in google_service_account.embedded_api : "api-${app_id}" => sa.email
    },
    {
      for app_id, sa in google_service_account.embedded_ui : "ui-${app_id}" => sa.email
    },
  )
  embedded_runtime_bindings = {
    for pair in setproduct(keys(local.embedded_runtime_members), [
      "roles/logging.logWriter",
      "roles/cloudtrace.agent",
      "roles/monitoring.metricWriter",
      ]) : "${pair[0]}|${pair[1]}" => {
      member = local.embedded_runtime_members[pair[0]]
      role   = pair[1]
    }
  }
}

resource "google_project_iam_member" "embedded_runtime_baseline" {
  for_each = local.embedded_runtime_bindings
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${each.value.member}"
}

locals {
  computed_portal_iap_audience = "/projects/${data.google_project.current.number}/global/backendServices/${google_compute_backend_service.portal.generated_id}"
  iap_backends = {
    portal = google_compute_backend_service.portal.name
    rm     = google_compute_backend_service.rm_shell.name
    ops    = google_compute_backend_service.ops_shell.name
  }
  iap_access_bindings = {
    for binding in setproduct(keys(local.iap_backends), var.iap_members) :
    "${binding[0]}|${binding[1]}" => {
      backend = binding[0]
      member  = binding[1]
    }
  }
}

resource "terraform_data" "iap_access_contract" {
  input = {
    configured_audience = var.iap_jwt_audience
    computed_audience   = local.computed_portal_iap_audience
    members             = var.iap_members
  }
  lifecycle {
    precondition {
      condition     = length(var.iap_members) == 0 || var.iap_jwt_audience != ""
      error_message = "Bootstrap must use no IAP members; grant access only after supplying the stage-one audience output."
    }
    precondition {
      condition     = length(var.iap_members) == 0 || var.iap_jwt_audience == local.computed_portal_iap_audience
      error_message = "iap_jwt_audience must exactly equal computed_portal_iap_audience before any access grant."
    }
  }
}

resource "google_iap_web_backend_service_iam_member" "access" {
  for_each            = local.iap_access_bindings
  project             = var.project_id
  web_backend_service = local.iap_backends[each.value.backend]
  role                = "roles/iap.httpsResourceAccessor"
  member              = each.value.member
  depends_on          = [terraform_data.iap_access_contract]
}
