resource "google_compute_region_network_endpoint_group" "portal" {
  project               = var.project_id
  name                  = "${var.name_prefix}-portal"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.portal.name
  }
}

resource "google_compute_region_network_endpoint_group" "shell" {
  for_each              = local.shells
  project               = var.project_id
  name                  = "${var.name_prefix}-${each.key}"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.shell[each.key].name
  }
}

resource "google_compute_backend_service" "portal" {
  project               = var.project_id
  name                  = "${var.name_prefix}-portal"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.portal.id
  }
  iap {
    enabled              = true
    oauth2_client_id     = var.iap_oauth2_client_id
    oauth2_client_secret = var.iap_oauth2_client_secret
  }
  log_config {
    enable      = true
    sample_rate = var.lb_log_sample_rate
  }
}

resource "google_compute_backend_service" "shell" {
  for_each              = local.shells
  project               = var.project_id
  name                  = "${var.name_prefix}-${each.key}"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.shell[each.key].id
  }
  iap {
    enabled              = true
    oauth2_client_id     = var.iap_oauth2_client_id
    oauth2_client_secret = var.iap_oauth2_client_secret
  }
  log_config {
    enable      = true
    sample_rate = var.lb_log_sample_rate
  }
}

resource "google_compute_url_map" "portal" {
  count           = var.production_edge_enabled ? 1 : 0
  project         = var.project_id
  name            = "${var.name_prefix}-journeys"
  default_service = google_compute_backend_service.shell[local.primary_shell].id

  # One host rule and one path matcher per shell, in var.shells order. Every host sends the
  # BFF's paths to the portal backend and everything else to its own shell.
  dynamic "host_rule" {
    for_each = var.shells
    content {
      hosts        = [host_rule.value.domain]
      path_matcher = host_rule.value.journey
    }
  }

  dynamic "path_matcher" {
    for_each = var.shells
    content {
      name            = path_matcher.value.journey
      default_service = google_compute_backend_service.shell[path_matcher.value.journey].id
      path_rule {
        paths   = ["/v1", "/v1/*", "/apps", "/apps/*", "/agent", "/agent/*", "/healthz"]
        service = google_compute_backend_service.portal.id
      }
    }
  }
}

# A managed certificate is replaced whenever its domain list changes -- a new shell host as
# much as a swapped one -- and the old one cannot be deleted while the HTTPS proxy still
# references it. So replacement must be create-before-destroy, and create-before-destroy needs a
# fresh name per domain list, which is what the keeper below mints. Without this, the first domain
# rotation fails with resourceInUseByAnotherResource (observed 2026-08-29 swapping the bootstrap
# domains for the minted LB address); with it, the 2026-09-03 rotation onto a new address was one
# apply. The keeper string is the domains in var.shells order, so a deployment that keeps the same
# shells in the same order keeps its certificate. README.md ("Adding a shell host") says what the
# replacement costs the hosts that were already live.
resource "random_id" "certificate" {
  count       = var.production_edge_enabled ? 1 : 0
  byte_length = 3
  keepers = {
    domains = join("|", local.shell_domains)
  }
}

resource "google_compute_managed_ssl_certificate" "portal" {
  count   = var.production_edge_enabled ? 1 : 0
  project = var.project_id
  name    = "${var.name_prefix}-journeys-${random_id.certificate[0].hex}"
  managed {
    domains = local.shell_domains
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_target_https_proxy" "portal" {
  count            = var.production_edge_enabled ? 1 : 0
  project          = var.project_id
  name             = "${var.name_prefix}-journeys"
  url_map          = google_compute_url_map.portal[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.portal[0].id]
}

resource "google_compute_global_address" "portal" {
  count   = var.production_edge_enabled ? 1 : 0
  project = var.project_id
  name    = "${var.name_prefix}-journeys"
}

resource "google_compute_global_forwarding_rule" "portal_https" {
  count                 = var.production_edge_enabled ? 1 : 0
  project               = var.project_id
  name                  = "${var.name_prefix}-https"
  target                = google_compute_target_https_proxy.portal[0].id
  ip_address            = google_compute_global_address.portal[0].address
  port_range            = "443"
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_dns_record_set" "portal" {
  for_each     = var.production_edge_enabled && var.dns_managed_zone != "" ? toset(local.shell_domains) : toset([])
  project      = var.project_id
  managed_zone = var.dns_managed_zone
  name         = "${each.value}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.portal[0].address]
}
