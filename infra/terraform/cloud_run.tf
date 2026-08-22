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
  deletion_protection = true

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
      env {
        name  = "PORTAL_IAP_AUDIENCE"
        value = var.iap_jwt_audience
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
  deletion_protection = true
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
  deletion_protection = true
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
  deletion_protection = true
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
      startup_probe {
        tcp_socket {
          port = each.value.ui_port
        }
      }
      liveness_probe {
        tcp_socket {
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
  deletion_protection = true
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
      dynamic "env" {
        for_each = each.value.api_env
        content {
          name  = env.key
          value = env.value
        }
      }
      dynamic "env" {
        for_each = contains(keys(local.embedded_iap_audience_env), each.key) ? {
          (local.embedded_iap_audience_env[each.key]) = var.iap_jwt_audience
        } : {}
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
