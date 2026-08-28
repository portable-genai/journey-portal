resource "google_compute_network" "portal" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-portal"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.services]
}

resource "google_compute_subnetwork" "portal" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-portal"
  region                   = var.region
  network                  = google_compute_network.portal.id
  ip_cidr_range            = var.portal_subnet_cidr
  private_ip_google_access = true
}

resource "google_compute_router" "portal" {
  project = var.project_id
  name    = "${var.name_prefix}-portal"
  region  = var.region
  network = google_compute_network.portal.id
}

resource "google_compute_router_nat" "portal" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-portal"
  region                             = var.region
  router                             = google_compute_router.portal.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.portal.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = var.nat_log_filter
  }
}
