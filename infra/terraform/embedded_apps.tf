# The ONE list of embedded apps this stack can deploy, and everything derived from it.
#
# Each entry names the two variables Terraform owns in that app's API container: the profile
# the app reads its adapter stack from (a deployment must set it to a managed profile) and the
# IAP audience Terraform injects. The set of deployable ids, the managed-profile check, the
# reserved environment names and the audience injection in cloud_run.tf all read this map.
#
# It used to be four hand-kept lists. The allowed-id list in variables.tf named sixteen apps,
# the profile map beside it named seven, and the error message named a third set. For the nine
# ids that were allowed but unmapped, the managed-profile lookup fell back to "" and failed by
# construction, so marketing-compliance-gate passed the id check and could never plan. An app
# is deployable when it has an entry here, and only then.
#
# The checks live in preconditions rather than in the variable's validation because the stack
# declares Terraform >= 1.7, and a validation block cannot read a local before 1.9.
# tests/test_terraform_source_contract.py holds this map equal to the deployment renderer's
# copy and fails if an app id or a managed variable name reappears in another .tf file.
locals {
  embedded_app_managed_env = {
    cdd-sow-research           = { profile = "CDD_PROFILE", iap_audience = "CDD_IAP_AUDIENCE" }
    credit-memo-drafting       = { profile = "CREDIT_MEMO_PROFILE", iap_audience = "CREDIT_MEMO_IAP_AUDIENCE" }
    cio-advisory               = { profile = "CIO_PROFILE", iap_audience = "CIO_IAP_AUDIENCE" }
    trade-finance-checker      = { profile = "TRADE_FINANCE_PROFILE", iap_audience = "TRADE_FINANCE_IAP_AUDIENCE" }
    loan-document-intelligence = { profile = "LOAN_DOC_PROFILE", iap_audience = "LOAN_DOC_IAP_AUDIENCE" }
    compliance-advisory        = { profile = "COMPLIANCE_PROFILE", iap_audience = "COMPLIANCE_IAP_AUDIENCE" }
    human-review-console       = { profile = "REVIEW_PROFILE", iap_audience = "REVIEW_IAP_AUDIENCE" }
    marketing-compliance-gate  = { profile = "MKT_GOV_PROFILE", iap_audience = "MKT_GOV_IAP_AUDIENCE" }
  }

  deployable_embedded_app_ids = sort(keys(local.embedded_app_managed_env))
  managed_embedded_profiles   = ["gcp", "platform"]

  # Names no deployment may supply on any surface: the ones Cloud Run sets itself, and every
  # profile and audience in the map above. An API may still set its OWN profile, because that
  # is the one managed choice the deployment makes.
  terraform_owned_embedded_env_names = toset(concat(
    ["PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"],
    [for app in values(local.embedded_app_managed_env) : app.profile],
    [for app in values(local.embedded_app_managed_env) : app.iap_audience],
  ))

  undeployable_embedded_apps = sort(tolist(setsubtract(
    toset(keys(var.embedded_apps)),
    toset(local.deployable_embedded_app_ids),
  )))

  # Each filter checks membership first, so an unmapped id is reported once, by the
  # precondition above, instead of failing an index lookup here.
  embedded_apps_without_managed_profile = [
    for id, app in var.embedded_apps :
    "${id} (${local.embedded_app_managed_env[id].profile})"
    if contains(local.deployable_embedded_app_ids, id) && !contains(
      local.managed_embedded_profiles,
      lookup(app.api_env, local.embedded_app_managed_env[id].profile, ""),
    )
  ]

  embedded_app_env_collisions = flatten([
    for id, app in var.embedded_apps : [
      for name in sort(tolist(setunion(
        setintersection(toset(keys(app.ui_env)), local.terraform_owned_embedded_env_names),
        setintersection(toset(keys(app.ui_secret_env)), local.terraform_owned_embedded_env_names),
        setintersection(toset(keys(app.api_secret_env)), local.terraform_owned_embedded_env_names),
        setintersection(
          toset(keys(app.api_env)),
          setsubtract(local.terraform_owned_embedded_env_names, toset([local.embedded_app_managed_env[id].profile])),
        ),
      ))) : "${id}.${name}"
    ]
    if contains(local.deployable_embedded_app_ids, id)
  ])

  # Cloud Run refuses a service name longer than 49 characters, and it refuses it at apply,
  # after the service accounts and IAM grants in the same plan have been created. The API name
  # is the longer of each app's two.
  embedded_service_names_over_limit = [
    for id in sort(keys(var.embedded_apps)) : "${var.name_prefix}-${id}-api"
    if length("${var.name_prefix}-${id}-api") > 49
  ]
}

resource "terraform_data" "embedded_app_contract" {
  input = {
    apps = sort(keys(var.embedded_apps))
  }
  lifecycle {
    precondition {
      condition     = length(local.undeployable_embedded_apps) == 0
      error_message = "embedded_apps names ${join(", ", local.undeployable_embedded_apps)}, which this stack cannot deploy: it has no managed profile or IAP audience variable for them. Deployable apps are ${join(", ", local.deployable_embedded_app_ids)}. Add an entry to local.embedded_app_managed_env to make an app deployable."
    }
    precondition {
      condition     = length(local.embedded_apps_without_managed_profile) == 0
      error_message = "Every deployed journey API must set its profile variable to ${join(" or ", local.managed_embedded_profiles)} in api_env: ${join(", ", local.embedded_apps_without_managed_profile)}."
    }
    precondition {
      condition     = length(local.embedded_app_env_collisions) == 0
      error_message = "Embedded app environment maps must not set Cloud Run-managed names, another app's profile, or a Terraform-injected IAP audience: ${join(", ", local.embedded_app_env_collisions)}."
    }
    precondition {
      condition     = length(local.embedded_service_names_over_limit) == 0
      error_message = "Cloud Run service names must be 49 characters or fewer; shorten name_prefix: ${join(", ", local.embedded_service_names_over_limit)}."
    }
  }
}
