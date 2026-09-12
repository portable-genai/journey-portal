# The ONE list of persona shells this deployment publishes, and everything derived from it.
#
# A shell is one journey on one hostname: a Cloud Run service running a static shell image built
# for that journey, its serverless NEG and IAP-enabled backend service, a host rule on the url map,
# a domain on the managed certificate, an optional DNS record, an IAP grant per approved member and
# a rollback component. Until 2026-09-12 every one of those was written out twice by hand, once as
# `rm_shell` and once as `ops_shell`, so the Marketing journey, which the catalog had composed since
# the persona journeys landed, had no host a deployment could publish it on. A fourth persona is an
# entry in var.shells; nothing below names a journey.
#
# var.shells is a LIST, not a map, and the order is load-bearing in exactly one place: the managed
# certificate's domain list. The provider treats that list as ordered and ForceNew, so reordering
# it replaces the certificate exactly as adding a domain does. A map would have sorted the two
# existing hosts into [ops, rm] and rotated the reference deployment's certificate on a change
# that added nothing. The first entry is also the url map's default service, the backend a request
# for an unrouted Host reaches.
locals {
  # Grouped (the trailing ...) so a journey named twice reaches the precondition below with its
  # own message, instead of dying in this expression as "Duplicate object key".
  shells_by_journey = { for shell in var.shells : shell.journey => shell... }
  shells            = { for journey, entries in local.shells_by_journey : journey => entries[0] }
  shell_journeys    = [for shell in var.shells : shell.journey]
  shell_domains     = [for shell in var.shells : shell.domain]
  primary_shell     = var.shells[0].journey

  # The journey catalog is the BFF's own config file, read here rather than restated: the set of
  # journeys a shell may claim, and the apps each composes, have one home.
  journey_catalog = yamldecode(file("${path.module}/../../config/journeys.yaml"))
  journey_apps    = { for key, journey in local.journey_catalog.journeys : key => journey.apps }

  shells_with_unknown_journey = [
    for shell in var.shells : shell.journey if !contains(keys(local.journey_apps), shell.journey)
  ]

  # A shell whose journey has no deployed app is the hazard e2e/app_coverage.py exists to catch:
  # the BFF drops a journey none of whose apps are mounted (PORTAL_APPS), and a shell built for a
  # journey the catalog no longer lists renders the FIRST journey instead -- a healthy page under
  # the wrong persona's hostname. Membership is checked first so an unknown journey is reported
  # once, above, rather than failing an index lookup here.
  shells_without_a_deployed_app = [
    for shell in var.shells : "${shell.journey} (${join(", ", local.journey_apps[shell.journey])})"
    if contains(keys(local.journey_apps), shell.journey) && length(setintersection(
      toset(local.journey_apps[shell.journey]),
      toset(keys(var.embedded_apps)),
    )) == 0
  ]
}

resource "terraform_data" "shell_contract" {
  input = {
    shells = local.shell_journeys
  }
  lifecycle {
    precondition {
      condition     = length(local.shell_journeys) == length(distinct(local.shell_journeys))
      error_message = "shells names a journey twice: one shell serves one journey on one hostname."
    }
    precondition {
      condition     = length(local.shell_domains) == length(distinct(local.shell_domains))
      error_message = "shells share a hostname: each shell owns its root path, so each needs a distinct domain."
    }
    precondition {
      condition     = length(local.shells_with_unknown_journey) == 0
      error_message = "shells names journeys the catalog (config/journeys.yaml) does not define: ${join(", ", local.shells_with_unknown_journey)}. The catalog defines ${join(", ", sort(keys(local.journey_apps)))}."
    }
    precondition {
      condition     = length(local.shells_without_a_deployed_app) == 0
      error_message = "Every shell's journey needs at least one of its apps in embedded_apps, or the BFF drops the journey and the shell renders another one under this hostname: ${join("; ", local.shells_without_a_deployed_app)}."
    }
  }
}

# The two shells that existed before var.shells, at the addresses the reference deployment's state
# holds them under. Terraform moves them in place; a plan for a deployment naming the same two
# shells shows no shell create or destroy.
moved {
  from = google_service_account.rm_shell
  to   = google_service_account.shell["rm"]
}
moved {
  from = google_service_account.ops_shell
  to   = google_service_account.shell["ops"]
}
moved {
  from = google_cloud_run_v2_service.rm_shell
  to   = google_cloud_run_v2_service.shell["rm"]
}
moved {
  from = google_cloud_run_v2_service.ops_shell
  to   = google_cloud_run_v2_service.shell["ops"]
}
moved {
  from = google_compute_region_network_endpoint_group.rm_shell
  to   = google_compute_region_network_endpoint_group.shell["rm"]
}
moved {
  from = google_compute_region_network_endpoint_group.ops_shell
  to   = google_compute_region_network_endpoint_group.shell["ops"]
}
moved {
  from = google_compute_backend_service.rm_shell
  to   = google_compute_backend_service.shell["rm"]
}
moved {
  from = google_compute_backend_service.ops_shell
  to   = google_compute_backend_service.shell["ops"]
}
moved {
  from = google_cloud_run_v2_service_iam_member.iap_rm_invoker
  to   = google_cloud_run_v2_service_iam_member.iap_shell_invoker["rm"]
}
moved {
  from = google_cloud_run_v2_service_iam_member.iap_ops_invoker
  to   = google_cloud_run_v2_service_iam_member.iap_shell_invoker["ops"]
}
