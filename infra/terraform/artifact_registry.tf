# artifact_registry.tf — The registry the portal and its embedded apps deploy from.
#
# Every image variable in this stack must be pinned by sha256 digest, yet the repository
# those digests live in was created by hand during the reference deployment and existed in
# no Terraform at all — a clean checkout could not rebuild the stack it describes. Declared
# here so Terraform is the only place infrastructure is described.
#
# Image layers carry the application and its configuration, so the repository is bound to
# the portal CMEK key. CMEK does not cascade: the Artifact Registry service agent needs its
# own key grant, and the agent does not exist until it is asked for — the identity below is
# asked for, not spelled out, exactly like the IAP agent in main.tf.

resource "google_project_service_identity" "artifactregistry" {
  provider = google-beta
  project  = var.project_id
  service  = "artifactregistry.googleapis.com"

  depends_on = [
    google_project_service.services["artifactregistry.googleapis.com"],
  ]
}

resource "google_kms_crypto_key_iam_member" "artifactregistry" {
  crypto_key_id = google_kms_crypto_key.portal.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.artifactregistry.email}"
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name_prefix
  description   = "Portal BFF, shell and embedded app images, digest-pinned, CMEK-encrypted."
  format        = "DOCKER"

  kms_key_name = google_kms_crypto_key.portal.id

  # Immutable tags: a deployed tag must always name the same bytes. The stack already
  # refuses any image not pinned by digest; this closes the same gap on the registry side.
  docker_config {
    immutable_tags = true
  }

  depends_on = [
    google_kms_crypto_key_iam_member.artifactregistry,
  ]
}

output "image_repository" {
  description = "Image prefix to build and push the portal's images into."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
