resource "google_logging_project_bucket_config" "audit" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "${var.name_prefix}-audit"
  retention_days = var.audit_retention_days
  locked         = var.lock_audit_bucket
  description    = "Hrz9 load-balancer, IAP, and Cloud Run audit evidence."
  cmek_settings {
    kms_key_name = google_kms_crypto_key.portal.id
  }
  depends_on = [
    google_kms_crypto_key_iam_member.logging,
    google_project_service.services,
  ]
}

resource "google_logging_project_sink" "audit" {
  project                = var.project_id
  name                   = "${var.name_prefix}-audit"
  destination            = "logging.googleapis.com/projects/${var.project_id}/locations/${var.region}/buckets/${google_logging_project_bucket_config.audit.bucket_id}"
  unique_writer_identity = true
  filter                 = "resource.type=(\"http_load_balancer\" OR \"cloud_run_revision\") OR protoPayload.serviceName=\"iap.googleapis.com\""
}

# NO bucketWriter grant, deliberately.
#
# A sink routing to a log bucket in its OWN project is given no writer identity — the field
# comes back empty even with unique_writer_identity = true — so this granted
# roles/logging.bucketWriter to the empty string and the apply failed with
# "invalid value \"\" for member" (2026-08-24). It cannot be made conditional either: `count`
# may not depend on a value that is unknown until apply.
#
# Nothing is lost by removing it. The confinement it was meant to provide holds by
# construction: same-project routing needs no cross-project grant. A writer identity — and
# this grant — would be required if the destination ever moved to another project, and that
# is the change that should reintroduce it.

resource "google_monitoring_alert_policy" "iap_denials" {
  project               = var.project_id
  display_name          = "${var.name_prefix} IAP denials"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "IAP authorization failures"
    condition_matched_log {
      filter = "resource.type=\"http_load_balancer\" AND httpRequest.status=403"
    }
  }
  alert_strategy {
    notification_rate_limit { period = "300s" }
    auto_close = "1800s"
  }
  depends_on = [google_project_service.services]
}

resource "google_monitoring_alert_policy" "service_account_key_creation" {
  project               = var.project_id
  display_name          = "${var.name_prefix} service-account key creation"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "Service-account key created or uploaded"
    condition_matched_log {
      filter = <<-EOT
        protoPayload.methodName=("google.iam.admin.v1.CreateServiceAccountKey" OR "google.iam.admin.v1.UploadServiceAccountKey")
      EOT
    }
  }
  alert_strategy {
    notification_rate_limit { period = "300s" }
    auto_close = "1800s"
  }
  depends_on = [google_project_service.services]
}

resource "google_monitoring_alert_policy" "vpc_sc_denials" {
  project               = var.project_id
  display_name          = "${var.name_prefix} VPC-SC denials"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "Service perimeter denied a request"
    condition_matched_log {
      filter = "protoPayload.metadata.@type=\"type.googleapis.com/google.cloud.audit.VpcServiceControlAuditMetadata\""
    }
  }
  alert_strategy {
    notification_rate_limit { period = "300s" }
    auto_close = "1800s"
  }
  depends_on = [google_project_service.services]
}

resource "google_monitoring_alert_policy" "cmek_changes" {
  project               = var.project_id
  display_name          = "${var.name_prefix} CMEK changes"
  combiner              = "OR"
  notification_channels = var.notification_channels
  conditions {
    display_name = "CMEK key or policy changed"
    condition_matched_log {
      filter = <<-EOT
        protoPayload.serviceName="cloudkms.googleapis.com"
        protoPayload.methodName=("CreateCryptoKeyVersion" OR "DestroyCryptoKeyVersion" OR "SetIamPolicy" OR "UpdateCryptoKey")
      EOT
    }
  }
  alert_strategy {
    notification_rate_limit { period = "300s" }
    auto_close = "1800s"
  }
  depends_on = [google_project_service.services]
}
