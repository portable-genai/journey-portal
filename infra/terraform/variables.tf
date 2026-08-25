variable "project_id" {
  type        = string
  description = "GCP project that hosts the complete portal journey."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid GCP project id."
  }
}

variable "name_prefix" {
  type        = string
  default     = "hrz9"
  description = "Short, stable prefix for deployed resources."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3 to 20 lowercase letters, digits, or hyphens."
  }
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = <<-EOT
    Region used by every regional service, SELECTED AT DEPLOY TIME. Validated against
    var.allowed_regions so an unapproved region fails fast at `terraform plan` rather than
    deploying data out of jurisdiction (P-03). main.tf carries the same check as a
    deployment-contract precondition.

    The default follows the portfolio region decision (org-metadata
    docs/deployment-region-alignment.md, recorded 2026-08-23 and REVISED 2026-08-24): the
    launch set co-locates, and that region is us-central1. It was asia-southeast1 until the
    revision. This default exists so an unset deploy agrees with the running reference
    deployment; it is NOT a residency recommendation. us-central1 satisfies no Asia-Pacific
    residency regime. An institution deploying in-country sets this and allowed_regions
    together, which is a reviewed input change and not a repository edit.
  EOT
  validation {
    condition     = contains(var.allowed_regions, var.region)
    error_message = "region must be one of var.allowed_regions (residency allowlist). Add it there first if that region is approved for this workload (P-03)."
  }
}

variable "allowed_regions" {
  type        = set(string)
  default     = ["us-central1"]
  description = <<-EOT
    Institution-approved residency allowlist: the regions this portal may be deployed to.
    The region is chosen at deploy time (var.region) and validated against this list to FAIL
    FAST (P-03). Extending it is the deliberate residency review point: confirm that every
    embedded app is configured for that region and that your residency obligations are met
    there first.

    The default is the single region the portfolio decision names, so an unset deploy cannot
    silently spread across regions and cannot disagree with var.region. Co-location is the
    rule: a deviation needs the service named, the reason it cannot run in-region, a
    data-flow record and the security owner's approval.
  EOT
  validation {
    condition     = length(var.allowed_regions) > 0
    error_message = "allowed_regions must list at least one residency-approved region."
  }
}

variable "apply_org_policies" {
  type        = bool
  default     = false
  description = "Apply project Org Policies only after the operator confirms orgpolicy authority."
}

variable "bff_image" {
  type        = string
  description = "BFF image pinned by sha256 digest."
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.bff_image))
    error_message = "bff_image must use an immutable @sha256 digest."
  }
}

variable "rm_shell_image" {
  type        = string
  description = "RM shell image pinned by sha256 digest."
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.rm_shell_image))
    error_message = "rm_shell_image must use an immutable @sha256 digest."
  }
}

variable "ops_shell_image" {
  type        = string
  description = "Ops shell image pinned by sha256 digest."
  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.ops_shell_image))
    error_message = "ops_shell_image must use an immutable @sha256 digest."
  }
}

variable "portal_audit_hmac_secret" {
  type        = string
  description = "Secret Manager name containing the portal access-audit pseudonym HMAC key."
  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,255}$", var.portal_audit_hmac_secret))
    error_message = "portal_audit_hmac_secret must be a Secret Manager resource name."
  }
}

variable "portal_audit_hmac_secret_version" {
  type        = string
  description = "Exact numeric Secret Manager version for the portal access-audit HMAC key."
  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.portal_audit_hmac_secret_version))
    error_message = "portal_audit_hmac_secret_version must be an exact numeric version."
  }
}

variable "observability_url" {
  type        = string
  description = "Exact HTTPS origin of the Hrz5 observability service."
  validation {
    condition     = can(regex("^https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", var.observability_url)) && !strcontains(var.observability_url, "..")
    error_message = "observability_url must be an exact lowercase DNS HTTPS origin without a path or explicit port."
  }
}

variable "observability_audience" {
  type        = string
  description = "Exact audience accepted by Hrz5 for the portal workload token."
  validation {
    condition     = can(regex("^https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", var.observability_audience)) && !strcontains(var.observability_audience, "..")
    error_message = "observability_audience must be an exact lowercase DNS HTTPS origin."
  }
}

variable "cloud_run_deletion_protection" {
  type        = bool
  description = <<-EOT
    Cloud Run deletion protection. True (the default) for anything that matters.

    Was hardcoded true on all five services, so the first image change failed mid-apply with
    "cannot destroy service without setting deletion_protection=false" — a half-applied stack
    blocked by a value nobody could set. A reference or evaluation stack that must stay
    replaceable sets this false deliberately.
  EOT
  default     = true
}

variable "embedded_apps" {
  type = map(object({
    ui_image           = string
    api_image          = string
    ui_build_base_path = string
    ui_port            = optional(number, 3000)
    api_port           = optional(number, 8080)
    ui_env             = optional(map(string), {})
    ui_secret_env      = optional(map(string), {})
    api_env            = optional(map(string), {})
    api_secret_env     = optional(map(string), {})
  }))
  description = "Reviewed embedded UI/API images and surface-specific runtime inputs keyed by app id."
  # A deployment names the SUBSET of journeys it actually serves. Requiring all seven on every
  # apply coupled seven independently-released repositories into one atomic deployment and made
  # a single-journey installation inexpressible — the opposite of the incremental adoption this
  # architecture argues for. The safety properties are unchanged: non-empty, known ids only,
  # digest-pinned images, correct mount path, valid ports.
  validation {
    condition = length(var.embedded_apps) > 0 && alltrue([
      for id, app in var.embedded_apps :
      contains(["doc1", "doc2", "doc3", "doc4", "doc5", "rsk1", "hrz7"], id) &&
      can(regex("@sha256:[0-9a-f]{64}$", app.ui_image)) &&
      can(regex("@sha256:[0-9a-f]{64}$", app.api_image)) &&
      app.ui_build_base_path == (id == "doc1" ? "/agent" : "/apps/${id}") &&
      app.ui_port >= 1 && app.ui_port <= 65535 &&
      app.api_port >= 1 && app.api_port <= 65535
    ])
    error_message = "embedded_apps must be a non-empty subset of doc1, doc2, doc3, doc4, doc5, rsk1, hrz7, each with a digest-pinned UI/API image, its canonical mount path, and valid ports."
  }
  # Each journey API reads its profile from its OWN env var, so the check is per-app and
  # applies only to the apps being deployed. Previously it dereferenced all seven directly,
  # which meant a partial deployment failed here even once the set check allowed it.
  validation {
    condition = alltrue([
      for id, app in var.embedded_apps :
      contains(["gcp", "platform"], try(app.api_env[{
        doc1 = "CDD_PROFILE"
        doc2 = "CREDIT_MEMO_PROFILE"
        doc3 = "CIO_PROFILE"
        doc4 = "TRADE_FINANCE_PROFILE"
        doc5 = "LOAN_DOC_PROFILE"
        rsk1 = "COMPLIANCE_PROFILE"
        hrz7 = "REVIEW_PROFILE"
      }[id]], ""))
    ])
    error_message = "Every DEPLOYED journey API must explicitly use the gcp or platform managed profile."
  }
  validation {
    condition = alltrue([
      for id, app in var.embedded_apps :
      length(setintersection(toset(keys(app.ui_env)), toset([
        "PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION",
        "CDD_PROFILE", "CREDIT_MEMO_PROFILE", "CIO_PROFILE",
        "TRADE_FINANCE_PROFILE", "LOAN_DOC_PROFILE", "COMPLIANCE_PROFILE", "REVIEW_PROFILE",
        "CDD_IAP_AUDIENCE", "CREDIT_MEMO_IAP_AUDIENCE", "CIO_IAP_AUDIENCE",
        "TRADE_FINANCE_IAP_AUDIENCE", "LOAN_DOC_IAP_AUDIENCE", "COMPLIANCE_IAP_AUDIENCE", "REVIEW_IAP_AUDIENCE",
      ]))) == 0 &&
      length(setintersection(toset(keys(app.ui_secret_env)), toset([
        "PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION",
        "CDD_PROFILE", "CREDIT_MEMO_PROFILE", "CIO_PROFILE",
        "TRADE_FINANCE_PROFILE", "LOAN_DOC_PROFILE", "COMPLIANCE_PROFILE", "REVIEW_PROFILE",
        "CDD_IAP_AUDIENCE", "CREDIT_MEMO_IAP_AUDIENCE", "CIO_IAP_AUDIENCE",
        "TRADE_FINANCE_IAP_AUDIENCE", "LOAN_DOC_IAP_AUDIENCE", "COMPLIANCE_IAP_AUDIENCE", "REVIEW_IAP_AUDIENCE",
      ]))) == 0 &&
      length(setintersection(toset(keys(app.api_secret_env)), toset([
        "PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION",
        "CDD_PROFILE", "CREDIT_MEMO_PROFILE", "CIO_PROFILE",
        "TRADE_FINANCE_PROFILE", "LOAN_DOC_PROFILE", "COMPLIANCE_PROFILE", "REVIEW_PROFILE",
        "CDD_IAP_AUDIENCE", "CREDIT_MEMO_IAP_AUDIENCE", "CIO_IAP_AUDIENCE",
        "TRADE_FINANCE_IAP_AUDIENCE", "LOAN_DOC_IAP_AUDIENCE", "COMPLIANCE_IAP_AUDIENCE", "REVIEW_IAP_AUDIENCE",
      ]))) == 0 &&
      length(setintersection(
        toset(keys(app.api_env)),
        setsubtract(
          toset([
            "PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION",
            "CDD_PROFILE", "CREDIT_MEMO_PROFILE", "CIO_PROFILE",
            "TRADE_FINANCE_PROFILE", "LOAN_DOC_PROFILE", "COMPLIANCE_PROFILE", "REVIEW_PROFILE",
            "CDD_IAP_AUDIENCE", "CREDIT_MEMO_IAP_AUDIENCE", "CIO_IAP_AUDIENCE",
            "TRADE_FINANCE_IAP_AUDIENCE", "LOAN_DOC_IAP_AUDIENCE", "COMPLIANCE_IAP_AUDIENCE", "REVIEW_IAP_AUDIENCE",
          ]),
          toset([{
            doc1 = "CDD_PROFILE"
            doc2 = "CREDIT_MEMO_PROFILE"
            doc3 = "CIO_PROFILE"
            doc4 = "TRADE_FINANCE_PROFILE"
            doc5 = "LOAN_DOC_PROFILE"
            rsk1 = "COMPLIANCE_PROFILE"
            hrz7 = "REVIEW_PROFILE"
          }[id]])
        )
      )) == 0 &&
      length(setintersection(toset(keys(app.ui_env)), toset(keys(app.ui_secret_env)))) == 0 &&
      length(setintersection(toset(keys(app.api_env)), toset(keys(app.api_secret_env)))) == 0
    ])
    error_message = "UI/API plain and secret env sources must not overlap or use Cloud Run-managed names, another app's profile, or Terraform-injected IAP audiences."
  }
}

variable "rollback_images" {
  type        = map(string)
  description = "Declared immutable rollback images. Registry retention is verified separately."
  validation {
    condition = alltrue([
      for component, image in var.rollback_images :
      can(regex("^[a-z0-9][a-z0-9-]{0,30}$", component)) &&
      can(regex("@sha256:[0-9a-f]{64}$", image))
    ]) && alltrue([for component in ["bff", "rm", "ops"] : contains(keys(var.rollback_images), component)])
    error_message = "rollback_images values must be digest-pinned component images."
  }
}

variable "rm_domain" {
  type        = string
  description = "DNS name for the RM shell, without scheme or path."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+[a-z0-9]$", var.rm_domain))
    error_message = "rm_domain must be a DNS hostname."
  }
}

variable "ops_domain" {
  type        = string
  description = "DNS name for the Ops shell, without scheme or path."
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+[a-z0-9]$", var.ops_domain))
    error_message = "ops_domain must be a DNS hostname."
  }
}

variable "dns_managed_zone" {
  type        = string
  default     = ""
  description = "Existing Cloud DNS managed-zone name. Empty emits the address without records."
}

variable "iap_oauth2_client_id" {
  type        = string
  description = "Institution-approved IAP OAuth client id."
}

variable "iap_oauth2_client_secret" {
  type        = string
  sensitive   = true
  description = "Institution-approved IAP OAuth client secret. Keep Terraform state encrypted."
}

variable "iap_jwt_audience" {
  type        = string
  default     = ""
  description = "Exact audience output by the stage-one plan. Leave empty only while iap_members is empty."
  validation {
    condition     = var.iap_jwt_audience == "" || can(regex("^/projects/[0-9]+/global/backendServices/[0-9]+$", var.iap_jwt_audience))
    error_message = "iap_jwt_audience must be empty for bootstrap or an exact IAP backend resource audience."
  }
}

variable "portal_subnet_cidr" {
  type        = string
  default     = "10.42.0.0/26"
  description = "Dedicated Direct VPC egress subnet for the BFF."
  validation {
    condition     = can(cidrnetmask(var.portal_subnet_cidr)) && try(tonumber(split("/", var.portal_subnet_cidr)[1]) <= 26, false)
    error_message = "portal_subnet_cidr must be a valid IPv4 CIDR with a /26 or larger address range."
  }
}

variable "iap_members" {
  type        = set(string)
  default     = []
  description = "Exact users/groups granted IAP access."
  validation {
    condition     = alltrue([for member in var.iap_members : can(regex("^(user|group|serviceAccount):[^[:space:]]+@[^[:space:]]+$", member))])
    error_message = "iap_members accepts explicit user:, group:, or serviceAccount: identities only."
  }
}

variable "frame_ancestors" {
  type        = set(string)
  default     = ["'self'"]
  description = "Exact CSP frame ancestors shared by BFF and shells."
  validation {
    condition     = length(var.frame_ancestors) > 0 && !contains(var.frame_ancestors, "*") && alltrue([for origin in var.frame_ancestors : origin == "'self'" || can(regex("^https://[^*/[:space:]]+$", origin))])
    error_message = "frame_ancestors must be 'self' or exact HTTPS origins, never wildcards."
  }
}

variable "cors_origins" {
  type        = set(string)
  default     = []
  description = "Exact cross-origin callers, when a deployment truly needs them."
  validation {
    condition     = !contains(var.cors_origins, "*") && alltrue([for origin in var.cors_origins : can(regex("^https://[^*/[:space:]]+$", origin))])
    error_message = "cors_origins must be exact HTTPS origins, never wildcards."
  }
}

variable "tenant_embed_policies" {
  type = map(object({
    tenant          = string
    hosts           = set(string)
    frame_ancestors = set(string)
    cors_origins    = set(string)
  }))
  description = "Reviewed host-bound framing and CORS policy for each tenant boundary."
  validation {
    condition = length(var.tenant_embed_policies) > 0 && alltrue([
      for policy_id, policy in var.tenant_embed_policies :
      can(regex("^[a-z0-9][a-z0-9._-]{0,127}$", policy_id)) &&
      can(regex("^[a-z0-9][a-z0-9._-]{0,127}$", policy.tenant)) &&
      length(policy.hosts) > 0 &&
      length(policy.frame_ancestors) > 0 &&
      alltrue([for host in policy.hosts : can(regex("^[a-z0-9][a-z0-9.-]+[a-z0-9]$", host))]) &&
      !contains(policy.frame_ancestors, "*") &&
      alltrue([for origin in policy.frame_ancestors : origin == "'self'" || (can(regex("^https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", origin)) && !strcontains(origin, ".."))]) &&
      !contains(policy.cors_origins, "*") &&
      alltrue([for origin in policy.cors_origins : can(regex("^https://[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$", origin)) && !strcontains(origin, "..")])
    ])
    error_message = "tenant_embed_policies requires exact tenant ids, DNS hosts and non-wildcard HTTPS frame/CORS origins."
  }
}

variable "runtime" {
  type = object({
    cpu           = string
    memory        = string
    concurrency   = number
    min_instances = number
    max_instances = number
    timeout       = string
  })
  default = {
    cpu           = "1"
    memory        = "512Mi"
    concurrency   = 40
    min_instances = 1
    max_instances = 10
    timeout       = "300s"
  }
  description = "Bounded Cloud Run sizing shared by the portal services."
  validation {
    condition     = var.runtime.concurrency >= 1 && var.runtime.concurrency <= 1000 && var.runtime.min_instances >= 0 && var.runtime.max_instances >= var.runtime.min_instances && var.runtime.max_instances <= 100 && can(regex("^[1-9][0-9]*s$", var.runtime.timeout))
    error_message = "runtime concurrency, scaling, and timeout are outside safe bounds."
  }
}

variable "audit_retention_days" {
  type        = number
  default     = 180
  description = "Cloud Logging bucket retention. Increasing may be irreversible after lock."
  validation {
    condition     = var.audit_retention_days >= 180 && var.audit_retention_days <= 3650
    error_message = "audit_retention_days must be between 180 and 3650."
  }
}

variable "lock_audit_bucket" {
  type        = bool
  default     = false
  description = "Irreversibly lock audit retention only after recovery and legal approval."
}

variable "notification_channels" {
  type        = list(string)
  default     = []
  description = "Pre-created Monitoring notification-channel resource names."
  validation {
    condition = (
      length(var.notification_channels) > 0 &&
      length(distinct(var.notification_channels)) == length(var.notification_channels) &&
      alltrue([
        for channel in var.notification_channels :
        can(regex("^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/notificationChannels/[0-9]+$", channel))
      ])
    )
    error_message = "notification_channels must contain distinct reviewed channel resource names."
  }
}

variable "cmek_rotation_period" {
  type        = string
  default     = "7776000s"
  description = "Automatic rotation period for the regional Hrz9 CMEK."
  validation {
    condition = (
      can(regex("^[0-9]+(\\.[0-9]{1,9})?s$", var.cmek_rotation_period)) &&
      try(tonumber(trimsuffix(var.cmek_rotation_period, "s")), 0) >= 86400 &&
      try(tonumber(trimsuffix(var.cmek_rotation_period, "s")), 3153600001) <= 3153600000
    )
    error_message = "cmek_rotation_period must be decimal seconds between 86400s and 3153600000s, with at most 9 fractional digits."
  }
}

variable "vpc_sc_access_policy_id" {
  type        = string
  default     = ""
  description = "Organization Access Context Manager policy id. Empty omits the perimeter."
  validation {
    condition     = var.vpc_sc_access_policy_id == "" || can(regex("^[0-9]+$", var.vpc_sc_access_policy_id))
    error_message = "vpc_sc_access_policy_id must be empty or numeric."
  }
}

variable "vpc_sc_enforced" {
  type        = bool
  default     = false
  description = "Reserved enforcement switch. The deployment contract currently rejects true."
}

variable "vpc_sc_restricted_services" {
  type = set(string)
  default = [
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ]
  description = "Data-bearing services protected by the Hrz9 service perimeter."
  validation {
    condition     = length(var.vpc_sc_restricted_services) > 0
    error_message = "vpc_sc_restricted_services must not be empty."
  }
}

variable "tenant_by_identity_domain" {
  type        = map(string)
  default     = {}
  description = <<-EOT
    Verified identity domain -> reviewed tenant id, for the managed identity adapter.

    The tenant used to be read straight off the assertion's hosted-domain claim, which assumed
    the institution's Workspace domain and the tenant id in tenant_embed_policies are the same
    string. On a real deployment they are not, so the host/tenant check compared a domain against
    a label, never matched, and denied every request. Every VALUE here must therefore name a
    tenant that tenant_embed_policies actually declares, which the validation below enforces:
    a mapping onto a tenant with no reviewed embed policy would resolve requests onto a tenant
    boundary nobody wrote down.

    Empty keeps the old behaviour (the tenant IS the domain). Non-empty makes the map exhaustive:
    a domain absent from it resolves to no tenant at all, rather than to itself.
  EOT
  validation {
    condition = alltrue([
      for domain, tenant in var.tenant_by_identity_domain :
      can(regex("^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$", domain)) &&
      can(regex("^[a-z0-9][a-z0-9._-]{0,127}$", tenant)) &&
      contains([for policy in values(var.tenant_embed_policies) : policy.tenant], tenant)
    ])
    error_message = "each tenant_by_identity_domain entry must map a DNS domain onto a tenant that tenant_embed_policies declares."
  }
}

variable "upstream_timeout_seconds" {
  type        = number
  default     = 240
  description = <<-EOT
    How long the BFF waits on an embedded app before giving up.

    The application default is tuned for an API call. An embedded app doing real work is not one:
    a CDD dossier reads documents, retrieves grounded passages and makes several model calls, and
    a 30-second proxy showed the browser a 500 for a request the app went on to answer 200.

    Must stay BELOW the Cloud Run request timeout, which is enforced below: a proxy timeout
    longer than the platform's own is a timeout that never fires, and the caller sees the
    platform's opaque termination instead of the proxy's own error.
  EOT
  validation {
    condition     = var.upstream_timeout_seconds > 0 && var.upstream_timeout_seconds < tonumber(trimsuffix(var.runtime.timeout, "s"))
    error_message = "upstream_timeout_seconds must be positive and strictly below runtime.timeout."
  }
}
