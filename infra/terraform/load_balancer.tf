resource "google_compute_region_network_endpoint_group" "portal" {
  project               = var.project_id
  name                  = "${var.name_prefix}-portal"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.portal.name
  }
}

resource "google_compute_region_network_endpoint_group" "rm_shell" {
  project               = var.project_id
  name                  = "${var.name_prefix}-rm"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.rm_shell.name
  }
}

resource "google_compute_region_network_endpoint_group" "ops_shell" {
  project               = var.project_id
  name                  = "${var.name_prefix}-ops"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = google_cloud_run_v2_service.ops_shell.name
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

resource "google_compute_backend_service" "rm_shell" {
  project               = var.project_id
  name                  = "${var.name_prefix}-rm"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.rm_shell.id
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

resource "google_compute_backend_service" "ops_shell" {
  project               = var.project_id
  name                  = "${var.name_prefix}-ops"
  protocol              = "HTTP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  backend {
    group = google_compute_region_network_endpoint_group.ops_shell.id
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
  default_service = google_compute_backend_service.rm_shell.id

  host_rule {
    hosts        = [var.rm_domain]
    path_matcher = "rm"
  }
  host_rule {
    hosts        = [var.ops_domain]
    path_matcher = "ops"
  }

  path_matcher {
    name            = "rm"
    default_service = google_compute_backend_service.rm_shell.id
    path_rule {
      paths   = ["/v1", "/v1/*", "/apps", "/apps/*", "/agent", "/agent/*", "/healthz"]
      service = google_compute_backend_service.portal.id
    }
  }
  path_matcher {
    name            = "ops"
    default_service = google_compute_backend_service.ops_shell.id
    path_rule {
      paths   = ["/v1", "/v1/*", "/apps", "/apps/*", "/agent", "/agent/*", "/healthz"]
      service = google_compute_backend_service.portal.id
    }
  }
}

# A managed certificate is replaced whenever its domain set changes, and the old one
# cannot be deleted while the HTTPS proxy still references it — so replacement must be
# create-before-destroy, and create-before-destroy needs a fresh name per domain set.
# Without this, the first domain rotation fails with resourceInUseByAnotherResource
# (observed 2026-08-29 swapping the bootstrap domains for the minted LB address).
resource "random_id" "certificate" {
  count       = var.production_edge_enabled ? 1 : 0
  byte_length = 3
  keepers = {
    domains = "${var.rm_domain}|${var.ops_domain}"
  }
}

resource "google_compute_managed_ssl_certificate" "portal" {
  count   = var.production_edge_enabled ? 1 : 0
  project = var.project_id
  name    = "${var.name_prefix}-journeys-${random_id.certificate[0].hex}"
  managed {
    domains = [var.rm_domain, var.ops_domain]
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
  for_each     = var.production_edge_enabled && var.dns_managed_zone != "" ? toset([var.rm_domain, var.ops_domain]) : toset([])
  project      = var.project_id
  managed_zone = var.dns_managed_zone
  name         = "${each.value}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.portal[0].address]
}
