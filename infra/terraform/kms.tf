resource "google_kms_key_ring" "portal" {
  project  = var.project_id
  name     = "${var.name_prefix}-portal"
  location = var.region
  depends_on = [
    google_project_service.services["cloudkms.googleapis.com"],
  ]
}

resource "google_kms_crypto_key" "portal" {
  name                       = "${var.name_prefix}-portal"
  key_ring                   = google_kms_key_ring.portal.id
  rotation_period            = var.cmek_rotation_period
  destroy_scheduled_duration = "2592000s"

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  cloud_run_service_agent = "serviceAccount:service-${data.google_project.current.number}@serverless-robot-prod.iam.gserviceaccount.com"
}

resource "google_kms_crypto_key_iam_member" "cloud_run" {
  crypto_key_id = google_kms_crypto_key.portal.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = local.cloud_run_service_agent
}

data "google_logging_project_cmek_settings" "portal" {
  project = var.project_id
  depends_on = [
    google_project_service.services["logging.googleapis.com"],
  ]
}

resource "google_kms_crypto_key_iam_member" "logging" {
  crypto_key_id = google_kms_crypto_key.portal.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = data.google_logging_project_cmek_settings.portal.service_account_id
}
