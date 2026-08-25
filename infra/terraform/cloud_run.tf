locals {
  app_upstream_env = merge([
    for id, app in var.embedded_apps : {
      "PORTAL_${upper(replace(id, "-", "_"))}_UI"  = google_cloud_run_v2_service.embedded_ui[id].uri
      "PORTAL_${upper(replace(id, "-", "_"))}_API" = google_cloud_run_v2_service.embedded_api[id].uri
    }
  ]...)
  embedded_iap_audience_env = {
    doc1 = "CDD_IAP_AUDIENCE"
    doc2 = "CREDIT_MEMO_IAP_AUDIENCE"
    doc3 = "CIO_IAP_AUDIENCE"
    doc4 = "TRADE_FINANCE_IAP_AUDIENCE"
    doc5 = "LOAN_DOC_IAP_AUDIENCE"
    rsk1 = "COMPLIANCE_IAP_AUDIENCE"
    hrz7 = "REVIEW_IAP_AUDIENCE"
  }
}

resource "google_cloud_run_v2_service" "portal" {
  project             = var.project_id
  name                = "${var.name_prefix}-portal"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = var.cloud_run_deletion_protection

  template {
    service_account                  = google_service_account.portal.email
    encryption_key                   = google_kms_crypto_key.portal.id
    timeout                          = var.runtime.timeout
    max_instance_request_concurrency = var.runtime.concurrency
    scaling {
      min_instance_count = var.runtime.min_instances
      max_instance_count = var.runtime.max_instances
    }
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = google_compute_network.portal.id
        subnetwork = google_compute_subnetwork.portal.id
      }
    }
    containers {
      image = var.bff_image
      resources {
        limits = { cpu = var.runtime.cpu, memory = var.runtime.memory }
      }
      ports {
        container_port = 8110
      }
      env {
        name  = "PORTAL_PROFILE"
        value = "platform"
      }
      env {
        name  = "PORTAL_REGION"
        value = var.region
      }
      # The residency allowlist travels WITH the deployment. Without this the container falls
      # back to its built-in default set, and a portfolio region decision the Terraform has
      # already accepted is then rejected at startup by the app — which is exactly how this
      # failed its first startup probe on 2026-08-24 ("PORTAL_REGION must be one of [...],
      # got 'us-central1'"). Terraform and the runtime must read the same allowlist.
      env {
        name  = "PORTAL_ALLOWED_REGIONS"
        value = join(",", var.allowed_regions)
      }
      # The apps this installation actually mounts. The journeys config is the CATALOGUE of
      # everything the portal can serve; without this the portal loads all of it and refuses
      # to start on the ones still carrying their local loopback defaults — journeys this
      # deployment never intended to serve.
      env {
        name  = "PORTAL_APPS"
        value = join(",", sort(keys(var.embedded_apps)))
      }
      # SET-BUT-EMPTY is a distinct input to this app, and a rejected one: it refuses to start
      # with "PORTAL_IAP_AUDIENCE is set but empty; unset it when the capability is
      # intentionally absent". That is a deliberate design — "nobody chose" must not silently
      # read as "chosen absent" for a security capability — and Terraform was setting the
      # variable unconditionally, so the empty value was passed through as if it were a value.
      #
      # It made the documented two-pass bootstrap impossible: the IAP audience is the backend
      # service's own id, so it does not exist until the first apply has created it, and that
      # first apply could therefore never bring the portal up. Omitting the variable entirely
      # until an audience exists is what "unset it" means.
      dynamic "env" {
        for_each = var.iap_jwt_audience == "" ? [] : [var.iap_jwt_audience]
        content {
          name  = "PORTAL_IAP_AUDIENCE"
          value = env.value
        }
      }
      # Same shape as the audience above: OMITTED when no map is reviewed, never rendered blank.
      # The app reads this variable in three states and refuses a set-but-empty value, so an
      # empty string here would be a boot failure rather than "no mapping configured".
      dynamic "env" {
        for_each = length(var.tenant_by_identity_domain) == 0 ? [] : [1]
        content {
          name  = "PORTAL_TENANT_DOMAINS_JSON"
          value = jsonencode(var.tenant_by_identity_domain)
        }
      }
      # How long the reverse proxy waits on an embedded app.
      #
      # The default is tuned for an API call, and an embedded app doing real work is not one: a
      # CDD dossier reads documents, retrieves grounded passages and makes several model calls,
      # and took 76 seconds here. The proxy gave up first, so the browser was shown a 500 for a
      # request the app went on to answer 200 -- the worst shape of failure, because both halves
      # look correct from their own logs.
      #
      # Capped below the Cloud Run request timeout by the validation on this variable: an
      # upstream timeout longer than the platform's own is a timeout that never fires.
      env {
        name  = "PORTAL_UPSTREAM_TIMEOUT"
        value = tostring(var.upstream_timeout_seconds)
      }
      env {
        name  = "PORTAL_FRAME_ANCESTORS"
        value = join(" ", sort(tolist(var.frame_ancestors)))
      }
      env {
        name  = "PORTAL_CORS_ORIGINS"
        value = join(",", sort(tolist(var.cors_origins)))
      }
      env {
        name  = "PORTAL_TENANT_EMBED_POLICIES_JSON"
        value = local.tenant_embed_policies_json
      }
      env {
        name  = "PORTAL_OBSERVABILITY_URL"
        value = var.observability_url
      }
      env {
        name  = "PORTAL_OBSERVABILITY_AUDIENCE"
        value = var.observability_audience
      }
      env {
        name = "PORTAL_AUDIT_HMAC_KEY"
        value_source {
          secret_key_ref {
            secret  = var.portal_audit_hmac_secret
            version = var.portal_audit_hmac_secret_version
          }
        }
      }
      dynamic "env" {
        for_each = local.app_upstream_env
        content {
          name  = env.key
          value = env.value
        }
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }
  depends_on = [
    google_kms_crypto_key_iam_member.cloud_run,
    google_project_iam_member.portal_log_writer,
    google_project_service.services,
    google_secret_manager_secret_iam_member.portal_audit_hmac_access,
    terraform_data.deployment_contract,
  ]
}

resource "google_cloud_run_v2_service" "rm_shell" {
  project             = var.project_id
  name                = "${var.name_prefix}-rm"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = var.cloud_run_deletion_protection
  template {
    service_account                  = google_service_account.rm_shell.email
    encryption_key                   = google_kms_crypto_key.portal.id
    timeout                          = var.runtime.timeout
    max_instance_request_concurrency = var.runtime.concurrency
    scaling {
      min_instance_count = var.runtime.min_instances
      max_instance_count = var.runtime.max_instances
    }
    containers {
      image = var.rm_shell_image
      resources {
        limits = { cpu = var.runtime.cpu, memory = var.runtime.memory }
      }
      ports {
        container_port = 8080
      }
      env {
        name  = "FRAME_ANCESTORS"
        value = join(" ", sort(tolist(var.frame_ancestors)))
      }
      env {
        name  = "TENANT_EMBED_POLICIES_JSON"
        value = local.tenant_embed_policies_json
      }
      startup_probe {
        http_get {
          path = "/"
        }
      }
      liveness_probe {
        http_get {
          path = "/"
        }
      }
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.cloud_run, google_project_service.services, terraform_data.deployment_contract]
}

resource "google_cloud_run_v2_service" "ops_shell" {
  project             = var.project_id
  name                = "${var.name_prefix}-ops"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"
  deletion_protection = var.cloud_run_deletion_protection
  template {
    service_account                  = google_service_account.ops_shell.email
    encryption_key                   = google_kms_crypto_key.portal.id
    timeout                          = var.runtime.timeout
    max_instance_request_concurrency = var.runtime.concurrency
    scaling {
      min_instance_count = var.runtime.min_instances
      max_instance_count = var.runtime.max_instances
    }
    containers {
      image = var.ops_shell_image
      resources {
        limits = { cpu = var.runtime.cpu, memory = var.runtime.memory }
      }
      ports {
        container_port = 8080
      }
      env {
        name  = "FRAME_ANCESTORS"
        value = join(" ", sort(tolist(var.frame_ancestors)))
      }
      env {
        name  = "TENANT_EMBED_POLICIES_JSON"
        value = local.tenant_embed_policies_json
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.cloud_run, google_project_service.services, terraform_data.deployment_contract]
}

resource "google_cloud_run_v2_service" "embedded_ui" {
  for_each            = var.embedded_apps
  project             = var.project_id
  name                = "${var.name_prefix}-${each.key}-ui"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = var.cloud_run_deletion_protection
  template {
    service_account                  = google_service_account.embedded_ui[each.key].email
    encryption_key                   = google_kms_crypto_key.portal.id
    timeout                          = var.runtime.timeout
    max_instance_request_concurrency = var.runtime.concurrency
    scaling {
      min_instance_count = var.runtime.min_instances
      max_instance_count = var.runtime.max_instances
    }
    containers {
      image = each.value.ui_image
      resources {
        limits = { cpu = var.runtime.cpu, memory = var.runtime.memory }
      }
      ports {
        container_port = each.value.ui_port
      }
      dynamic "env" {
        for_each = each.value.ui_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.value.ui_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      # A TCP startup probe is fine and is the right check for "has the server bound its
      # port yet". A TCP LIVENESS probe is not: Cloud Run rejects the service outright with
      # "Cloud Run currently does not support TCP socket in liveness probe", so this shipped
      # in a shape the platform refuses. Liveness asks a different question anyway — "is the
      # app still answering?" — which a bound socket cannot tell you.
      startup_probe {
        tcp_socket {
          port = each.value.ui_port
        }
      }
      liveness_probe {
        http_get {
          path = each.value.ui_build_base_path
          port = each.value.ui_port
        }
      }
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.cloud_run, google_project_service.services, terraform_data.deployment_contract]
}

resource "google_cloud_run_v2_service" "embedded_api" {
  for_each            = var.embedded_apps
  project             = var.project_id
  name                = "${var.name_prefix}-${each.key}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = var.cloud_run_deletion_protection
  template {
    service_account                  = google_service_account.embedded_api[each.key].email
    encryption_key                   = google_kms_crypto_key.portal.id
    timeout                          = var.runtime.timeout
    max_instance_request_concurrency = var.runtime.concurrency
    scaling {
      min_instance_count = var.runtime.min_instances
      max_instance_count = var.runtime.max_instances
    }
    containers {
      image = each.value.api_image
      resources {
        limits = { cpu = var.runtime.cpu, memory = var.runtime.memory }
      }
      ports {
        container_port = each.value.api_port
      }
      # Every embedded app runs IN this project, so it should never have to be told which one
      # by hand. Left unset, an app that defaults its project id to a documented placeholder
      # carries that placeholder all the way into a live API call: the deployed Doc1 answered
      # 500 with "projects/your-gcp-project does not exist" on the first dossier build, which
      # reads as a broken app rather than a missing environment variable. Declared BEFORE
      # api_env, so a deployment that needs a different project can still say so.
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      dynamic "env" {
        for_each = each.value.api_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        # Same rule for the embedded apps: omit the audience rather than passing an empty
        # string, which their own config layers also treat as a rejected value.
        for_each = (
          contains(keys(local.embedded_iap_audience_env), each.key) && var.iap_jwt_audience != ""
          ? { (local.embedded_iap_audience_env[each.key]) = var.iap_jwt_audience }
          : {}
        )
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = each.value.api_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }
  depends_on = [google_kms_crypto_key_iam_member.cloud_run, google_project_service.services, terraform_data.deployment_contract]
}
