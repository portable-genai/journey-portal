output "rm_url" {
  value       = "https://${var.rm_domain}"
  description = "IAP-protected RM journey URL."
}

output "ops_url" {
  value       = "https://${var.ops_domain}"
  description = "IAP-protected Ops journey URL."
}

output "load_balancer_address" {
  value       = google_compute_global_address.portal.address
  description = "Address to publish in external DNS when dns_managed_zone is empty."
}

output "image_digests" {
  value = merge(
    { bff = var.bff_image, rm = var.rm_shell_image, ops = var.ops_shell_image },
    { for id, app in var.embedded_apps : "${id}-ui" => app.ui_image },
    { for id, app in var.embedded_apps : "${id}-api" => app.api_image },
  )
  description = "Exact immutable images reviewed for this deployment."
}

output "rollback_image_digests" {
  value       = var.rollback_images
  description = "Declared immutable rollback images; this output does not guarantee registry retention."
}

output "cmek_key" {
  value       = google_kms_crypto_key.portal.id
  description = "Regional CMEK bound to Cloud Run revisions and the audit bucket."
}

output "vpc_sc_mode" {
  value = (
    var.vpc_sc_access_policy_id == "" ? "not-configured" :
    var.vpc_sc_enforced ? "enforced" : "dry-run"
  )
  description = "Service-perimeter posture for this plan."
}

output "service_accounts" {
  value = merge(
    {
      portal = google_service_account.portal.email
      rm     = google_service_account.rm_shell.email
      ops    = google_service_account.ops_shell.email
    },
    { for id, account in google_service_account.embedded_ui : "${id}-ui" => account.email },
    { for id, account in google_service_account.embedded_api : "${id}-api" => account.email },
  )
  description = "Runtime identities for least-privilege review."
}

output "computed_portal_iap_audience" {
  value       = local.computed_portal_iap_audience
  description = "Exact audience to copy into iap_jwt_audience for the access-enabling second apply."
}

output "embedded_service_urls" {
  value = {
    for id, app in var.embedded_apps : id => {
      ui  = google_cloud_run_v2_service.embedded_ui[id].uri
      api = google_cloud_run_v2_service.embedded_api[id].uri
    }
  }
  description = "Private embedded-service origins used by the BFF."
}

output "verification_commands" {
  value = {
    unauthenticated_rm = "curl -I https://${var.rm_domain}"
    authenticated_rm   = "gcloud iap web login --resource-type=backend-services"
    revisions          = "gcloud run services list --region=${var.region} --project=${var.project_id}"
  }
  description = "Operator starting points. Execute and retain output in the named evidence pack."
}
