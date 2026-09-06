"""Fail-closed loader for a named journey-portal deployment configuration.

Non-secret deployment values belong in ``.env``. Secret values belong in
``.env.secrets``. The loader validates the complete contract before producing
Terraform inputs, and refuses examples, placeholders, mutable images, wildcard
origins, or secrets in the non-secret file.
"""

from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]+[a-z0-9]$")
_DIGEST_IMAGE_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
_AUDIENCE_RE = re.compile(r"^/projects/[0-9]+/global/backendServices/[0-9]+$")
_CHANNEL_RE = re.compile(r"^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/notificationChannels/[0-9]+$")
# ``pending``, ``tbd``, ``todo`` and ``changeme`` are here because the named-deployment dossier
# uses them as the honest marker for a decision nobody has made yet. cdd-sow-research's preflight
# already
# rejects the same set; without them a dossier row could be copied verbatim into `.env` and pass.
_PLACEHOLDER_MARKERS = (
    "replace_me",
    "replace-me",
    "placeholder",
    "change_me",
    "change-me",
    "changeme",
    "pending",
    "tbd",
    "todo",
    "your-",
    "<",
    ">",
    ".example.test",
)
# Every journey app id this portal knows how to mount. It is a VOCABULARY, not a shipping
# list: a deployment names the subset it actually serves, and an id outside this set is a
# typo rather than a new product.
#
# This was previously "exactly these seven", enforced on every deployment. That coupled seven
# independently-released repositories into one atomic deployment: the portal could not be
# stood up until all seven verticals were simultaneously deployable, and any one of them
# lagging blocked the other six. It also made a single-journey installation — a bank
# licensing one vertical — inexpressible, which is the opposite of the incremental adoption
# this architecture argues for everywhere else.
#
# The safety properties that rule was carrying are kept, and they are the ones that matter:
# the set may not be empty, every id must be known, every app must be complete and pinned to
# an immutable digest, and the rollback map must cover exactly what is being deployed. What
# is dropped is only the requirement that the portfolio be deployed all at once.
_KNOWN_JOURNEY_APPS = frozenset(
    {
        "cdd-sow-research",
        "credit-memo-drafting",
        "cio-advisory",
        "trade-finance-checker",
        "loan-document-intelligence",
        "compliance-advisory",
        "human-review-console",
    }
)
_EMBEDDED_APP_KEYS = frozenset(
    {
        "ui_image",
        "api_image",
        "ui_build_base_path",
        "ui_port",
        "api_port",
        "ui_env",
        "ui_secret_env",
        "api_env",
        "api_secret_env",
    }
)
_UI_BUILD_BASE_PATH_BY_APP = {
    "cdd-sow-research": "/agent",
    "credit-memo-drafting": "/apps/credit-memo-drafting",
    "cio-advisory": "/apps/cio-advisory",
    "trade-finance-checker": "/apps/trade-finance-checker",
    "loan-document-intelligence": "/apps/loan-document-intelligence",
    "compliance-advisory": "/apps/compliance-advisory",
    "human-review-console": "/apps/human-review-console",
}
_PROFILE_ENV_BY_APP = {
    "cdd-sow-research": "CDD_PROFILE",
    "credit-memo-drafting": "CREDIT_MEMO_PROFILE",
    "cio-advisory": "CIO_PROFILE",
    "trade-finance-checker": "TRADE_FINANCE_PROFILE",
    "loan-document-intelligence": "LOAN_DOC_PROFILE",
    "compliance-advisory": "COMPLIANCE_PROFILE",
    "human-review-console": "REVIEW_PROFILE",
}
_IAP_AUDIENCE_ENV_BY_APP = {
    "cdd-sow-research": "CDD_IAP_AUDIENCE",
    "credit-memo-drafting": "CREDIT_MEMO_IAP_AUDIENCE",
    "cio-advisory": "CIO_IAP_AUDIENCE",
    "trade-finance-checker": "TRADE_FINANCE_IAP_AUDIENCE",
    "loan-document-intelligence": "LOAN_DOC_IAP_AUDIENCE",
    "compliance-advisory": "COMPLIANCE_IAP_AUDIENCE",
    "human-review-console": "REVIEW_IAP_AUDIENCE",
}
_CLOUD_RUN_MANAGED_ENV_NAMES = frozenset({"PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION"})
_ALL_PROFILE_ENV_NAMES = frozenset(_PROFILE_ENV_BY_APP.values())
_ALL_IAP_AUDIENCE_ENV_NAMES = frozenset(_IAP_AUDIENCE_ENV_BY_APP.values())
_ALL_MANAGED_ENV_NAMES = (
    _CLOUD_RUN_MANAGED_ENV_NAMES | _ALL_PROFILE_ENV_NAMES | _ALL_IAP_AUDIENCE_ENV_NAMES
)
_DURATION_RE = re.compile(r"^[0-9]+(?:\.[0-9]{1,9})?s$")
# Mirrors the nat_log_filter validation in infra/terraform/variables.tf. Both halves refuse,
# because an operator can change this file without an apply and Terraform can be applied
# without this file.
_NAT_LOG_FILTERS = frozenset({"ALL", "ERRORS_ONLY", "TRANSLATIONS_ONLY"})
_MIN_KMS_ROTATION_SECONDS = Decimal(86_400)
_MAX_KMS_ROTATION_SECONDS = Decimal(3_153_600_000)
_OWNER_KEYS = (
    "DEPLOYMENT_OWNER",
    "SECURITY_OWNER",
    "DNS_IAP_OWNER",
    "OPERATIONS_OWNER",
    "EVIDENCE_APPROVER",
)

SECRET_KEYS = frozenset({"DEPLOY_IAP_OAUTH_CLIENT_SECRET"})
#: Secret payloads that exist only for a cdd-sow-research Mode 5 registration, so they are demanded
#: only when
#: one is configured. The BFF SIGNING key is never here: it is a non-exportable Cloud KMS key
#: version, and the only thing the deployment names is that version.
MODE5_SECRET_KEYS = frozenset({"PORTAL_SESSION_SIGNING_KEY"})
REQUIRED_NONSECRET_KEYS = frozenset(
    {
        "GCP_PROJECT_ID",
        "GCP_REGION",
        "GCP_ALLOWED_REGIONS_JSON",
        "DEPLOY_NAME_PREFIX",
        "DEPLOY_BFF_IMAGE",
        "DEPLOY_RM_SHELL_IMAGE",
        "DEPLOY_OPS_SHELL_IMAGE",
        "DEPLOY_EMBEDDED_APPS_JSON",
        "DEPLOY_ROLLBACK_IMAGES_JSON",
        "DEPLOY_RM_DOMAIN",
        "DEPLOY_OPS_DOMAIN",
        "DEPLOY_TENANT_ID",
        "DEPLOY_TENANT_IDENTITY_DOMAINS_JSON",
        "DEPLOY_OBSERVABILITY_URL",
        "DEPLOY_OBSERVABILITY_AUDIENCE",
        "DEPLOY_DNS_MANAGED_ZONE",
        "DEPLOY_TLS_MODE",
        "DEPLOY_IAP_OAUTH_CLIENT_ID",
        "DEPLOY_PORTAL_AUDIT_HMAC_SECRET",
        "DEPLOY_PORTAL_AUDIT_HMAC_SECRET_VERSION",
        "DEPLOY_IAP_JWT_AUDIENCE",
        "DEPLOY_IAP_MEMBERS_JSON",
        "DEPLOY_FRAME_ANCESTORS_JSON",
        "DEPLOY_CORS_ORIGINS_JSON",
        "DEPLOY_NOTIFICATION_CHANNELS_JSON",
        "DEPLOY_APPLY_ORG_POLICIES",
        "DEPLOY_VPC_SC_ACCESS_POLICY_ID",
        "DEPLOY_VPC_SC_ENFORCED",
        "DEPLOY_CMEK_ROTATION_PERIOD",
        "DEPLOY_AUDIT_RETENTION_DAYS",
        "DEPLOY_LOCK_AUDIT_BUCKET",
        "DEPLOY_CLOUD_RUN_DELETION_PROTECTION",
        # The deployment's own cost posture. Required rather than optional, and required for
        # the reason the WORM lock next door was NOT: a default that a deployment never states
        # is a decision nobody made. These four are the whole of this stack's standing spend
        # that a deployment can actually decline, so it declines them here or says it keeps
        # them.
        #
        # The edge joined them on 2026-09-02 and is the largest of the four. It used to be
        # unconditional in Terraform, so it was standing spend no deployment could decline and
        # none of them recorded -- the reference deployment tore it down by hand instead, which
        # left the config still declaring an edge that no longer existed.
        "DEPLOY_RUN_MIN_INSTANCES",
        "DEPLOY_LB_LOG_SAMPLE_RATE",
        "DEPLOY_NAT_LOG_FILTER",
        "DEPLOY_PRODUCTION_EDGE_ENABLED",
        "DEPLOY_TERRAFORM_STATE_BUCKET",
        "DEPLOY_TERRAFORM_STATE_PREFIX",
        "DEPLOYMENT_OWNER",
        "SECURITY_OWNER",
        "DNS_IAP_OWNER",
        "OPERATIONS_OWNER",
        "EVIDENCE_APPROVER",
    }
)
# The cdd-sow-research Mode 5 brokered-grant registration. All-or-nothing rather than required: a
# portal
# deployment that fronts no Mode 5 installation has nothing to register, while a HALF-configured
# registration is exactly the shape that authenticates to the wrong endpoint or under a client
# nobody reviewed. Naming any one of these therefore demands all of them.
MODE5_NONSECRET_KEYS = frozenset(
    {
        "PORTAL_PUBLIC_ORIGIN",
        "PORTAL_DOC1_GRANT_ENDPOINT",
        "PORTAL_DOC1_INSTALLATION_ID",
        "PORTAL_DOC1_BFF_CLIENT_ID",
        "PORTAL_BFF_SIGNING_KEY_VERSION",
        "PORTAL_BFF_SIGNING_KID",
    }
)
# Recorded and optional even inside a Mode 5 registration: the requested scopes have a reviewed
# default, and the accepted-key window is empty except during a rotation.
MODE5_OPTIONAL_KEYS = frozenset({"PORTAL_DOC1_REQUESTED_SCOPES", "PORTAL_BFF_ACCEPTED_PUBLIC_JWKS"})
OPTIONAL_NONSECRET_KEYS = (
    frozenset({"PORTAL_PROFILE", "PORTAL_JOURNEYS", "PORTAL_ALLOW_INSECURE_DEMO"})
    | MODE5_NONSECRET_KEYS
    | MODE5_OPTIONAL_KEYS
)


class DeploymentConfigError(ValueError):
    """The named deployment configuration is absent or unsafe."""


@dataclass(frozen=True, slots=True)
class DeploymentConfig:
    """Validated source values and the non-secret Terraform input document."""

    values: dict[str, str]
    secrets: dict[str, str]
    terraform_inputs: dict[str, Any]


def load_env_file(path: Path) -> dict[str, str]:
    """Read a small dotenv subset without interpolation or shell evaluation."""

    if not path.is_file():
        raise DeploymentConfigError(f"required environment file does not exist: {path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise DeploymentConfigError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise DeploymentConfigError(f"{path}:{line_number}: invalid variable name {key!r}")
        if key in values:
            raise DeploymentConfigError(f"{path}:{line_number}: duplicate variable {key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _require_keys(values: dict[str, str], required: frozenset[str], source: str) -> None:
    missing = sorted(required - values.keys())
    if missing:
        raise DeploymentConfigError(f"{source} is missing: {', '.join(missing)}")


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return not value.strip() or any(marker in lowered for marker in _PLACEHOLDER_MARKERS)
    if isinstance(value, dict):
        return any(
            _contains_placeholder(key) or _contains_placeholder(item) for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    return False


def _reject_placeholder(name: str, value: Any, *, allow_empty: bool = False) -> None:
    if allow_empty and value == "":
        return
    if _contains_placeholder(value):
        raise DeploymentConfigError(f"{name} still contains a placeholder or fictional value")


def _json_value(values: dict[str, str], name: str, expected: type[Any]) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise DeploymentConfigError(f"{name} contains duplicate object key {key!r}")
            result[key] = item
        return result

    try:
        result = json.loads(values[name], object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise DeploymentConfigError(f"{name} must contain valid JSON: {exc.msg}") from exc
    if not isinstance(result, expected):
        raise DeploymentConfigError(f"{name} must contain a JSON {expected.__name__}")
    return result


def _boolean(values: dict[str, str], name: str) -> bool:
    normalized = values[name].lower()
    if normalized not in {"true", "false"}:
        raise DeploymentConfigError(f"{name} must be true or false")
    return normalized == "true"


def _validate_images(images: dict[str, Any], name: str) -> None:
    if not images:
        raise DeploymentConfigError(f"{name} must not be empty")
    for component, image in images.items():
        if not isinstance(component, str) or not isinstance(image, str):
            raise DeploymentConfigError(f"{name} must map component names to image strings")
        _reject_placeholder(f"{name}.{component}", image)
        if not _DIGEST_IMAGE_RE.fullmatch(image):
            raise DeploymentConfigError(f"{name}.{component} must use an immutable @sha256 digest")


def _validate_embedded_apps(apps: dict[str, Any]) -> None:
    if not apps:
        raise DeploymentConfigError("DEPLOY_EMBEDDED_APPS_JSON must not be empty")
    unexpected_apps = sorted(apps.keys() - _KNOWN_JOURNEY_APPS)
    if unexpected_apps:
        raise DeploymentConfigError(
            "DEPLOY_EMBEDDED_APPS_JSON names apps this portal cannot mount: "
            f"{', '.join(unexpected_apps)}; known apps are "
            f"{', '.join(sorted(_KNOWN_JOURNEY_APPS))}"
        )
    for app_id, app in apps.items():
        # 63, not 21: the old bound fitted the four-character catalog codes these ids used to
        # be, so loan-document-intelligence was refused the moment ids became repository
        # names. 63 is the label limit the ids must satisfy anyway as Cloud Run service-name
        # components, which is the real constraint. `domain/catalog.py` carries the same bound.
        if not isinstance(app_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", app_id):
            raise DeploymentConfigError(f"unsafe embedded app id: {app_id!r}")
        if not isinstance(app, dict):
            raise DeploymentConfigError(f"embedded app {app_id} must be a JSON object")
        unexpected_keys = sorted(app.keys() - _EMBEDDED_APP_KEYS)
        if unexpected_keys:
            raise DeploymentConfigError(
                f"embedded app {app_id} has unknown keys: {', '.join(unexpected_keys)}"
            )
        for image_key in ("ui_image", "api_image"):
            image = app.get(image_key)
            if not isinstance(image, str):
                raise DeploymentConfigError(f"embedded app {app_id} is missing {image_key}")
            _reject_placeholder(f"{app_id}.{image_key}", image)
            if not _DIGEST_IMAGE_RE.fullmatch(image):
                raise DeploymentConfigError(
                    f"embedded app {app_id} {image_key} must use an immutable @sha256 digest"
                )
        expected_base_path = _UI_BUILD_BASE_PATH_BY_APP[app_id]
        if app.get("ui_build_base_path") != expected_base_path:
            raise DeploymentConfigError(
                f"embedded app {app_id} UI image must be built for {expected_base_path}"
            )
        for env_map_name in ("ui_env", "api_env"):
            env_map = app.get(env_map_name, {})
            if not isinstance(env_map, dict):
                raise DeploymentConfigError(f"embedded app {app_id} {env_map_name} must be a map")
            for env_name in env_map:
                if re.search(
                    r"(SECRET|TOKEN|PASSWORD|CREDENTIAL|PRIVATE[_-]?KEY|API[_-]?KEY|ACCESS[_-]?KEY)",
                    str(env_name),
                    re.IGNORECASE,
                ):
                    secret_map_name = f"{env_map_name.removesuffix('_env')}_secret_env"
                    raise DeploymentConfigError(
                        f"embedded app {app_id} {env_name} belongs in {secret_map_name}"
                    )
        api_env = app.get("api_env", {})
        profile_env = _PROFILE_ENV_BY_APP.get(app_id)
        if profile_env and api_env.get(profile_env) not in {"gcp", "platform"}:
            raise DeploymentConfigError(
                f"embedded app {app_id} must set {profile_env} to gcp or platform"
            )
        audience_env = _IAP_AUDIENCE_ENV_BY_APP.get(app_id)
        if audience_env and audience_env in api_env:
            raise DeploymentConfigError(
                f"embedded app {app_id} must not override Terraform-injected {audience_env}"
            )
        for secret_map_name in ("ui_secret_env", "api_secret_env"):
            secret_map = app.get(secret_map_name, {})
            if not isinstance(secret_map, dict):
                raise DeploymentConfigError(
                    f"embedded app {app_id} {secret_map_name} must be a map"
                )
            if not all(
                isinstance(secret_name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,255}", secret_name)
                for secret_name in secret_map.values()
            ):
                raise DeploymentConfigError(
                    f"embedded app {app_id} {secret_map_name} values must be Secret Manager names"
                )
        ui_env = app.get("ui_env", {})
        ui_secret_env = app.get("ui_secret_env", {})
        api_secret_env = app.get("api_secret_env", {})
        owning_profile = _PROFILE_ENV_BY_APP[app_id]
        api_env_reserved = _ALL_MANAGED_ENV_NAMES - {owning_profile}
        managed_collisions = sorted(
            (set(ui_env) | set(ui_secret_env) | set(api_secret_env)) & _ALL_MANAGED_ENV_NAMES
            | set(api_env) & api_env_reserved
        )
        source_collisions = sorted(
            set(ui_env) & set(ui_secret_env) | set(api_env) & set(api_secret_env)
        )
        if managed_collisions:
            raise DeploymentConfigError(
                f"embedded app {app_id} environment collides with managed names: "
                + ", ".join(managed_collisions)
            )
        if source_collisions:
            raise DeploymentConfigError(
                f"embedded app {app_id} plain and secret environment sources collide: "
                + ", ".join(source_collisions)
            )
        _reject_placeholder(f"embedded app {app_id}", app)


def _validate_mode5_registration(
    values: dict[str, str],
    secrets: dict[str, str],
    *,
    env_file: Path,
    secrets_file: Path,
) -> None:
    """A cdd-sow-research Mode 5 registration is complete or absent; a partial one is refused.

    Half a registration is worse than none: the portal would hold a service identity while
    pointing at an unreviewed endpoint, or publish a key id nothing signs with. The check is
    all-or-nothing on the naming, and every named value is put through the same placeholder
    rejection the required set gets, so ``PENDING`` cannot pass as a decision.
    """
    named = {key for key in MODE5_NONSECRET_KEYS if values.get(key, "").strip()}
    if not named and not secrets.get("PORTAL_SESSION_SIGNING_KEY", "").strip():
        return
    missing = sorted(MODE5_NONSECRET_KEYS - named)
    if missing:
        raise DeploymentConfigError(
            f"{env_file} configures a cdd-sow-research Mode 5 registration but leaves these unset: "
            f"{', '.join(missing)}. Complete the registration or remove it entirely."
        )
    if not secrets.get("PORTAL_SESSION_SIGNING_KEY", "").strip():
        raise DeploymentConfigError(
            f"a cdd-sow-research Mode 5 registration requires PORTAL_SESSION_SIGNING_KEY in "
            f"{secrets_file}: "
            "without it the portal can bind neither a CSRF token nor a session to a grant"
        )
    for key in sorted(MODE5_NONSECRET_KEYS | (MODE5_OPTIONAL_KEYS & values.keys())):
        _reject_placeholder(key, values[key])
    _reject_placeholder("PORTAL_SESSION_SIGNING_KEY", secrets["PORTAL_SESSION_SIGNING_KEY"])
    for key, prefix in (
        ("PORTAL_PUBLIC_ORIGIN", "https://"),
        ("PORTAL_DOC1_GRANT_ENDPOINT", "https://"),
    ):
        if not values[key].startswith(prefix):
            raise DeploymentConfigError(f"{key} must be an exact HTTPS value")
    if values["PORTAL_PUBLIC_ORIGIN"].rstrip("/") != values["PORTAL_PUBLIC_ORIGIN"]:
        raise DeploymentConfigError("PORTAL_PUBLIC_ORIGIN must carry no path or trailing slash")
    if not values["PORTAL_BFF_SIGNING_KEY_VERSION"].startswith("projects/"):
        raise DeploymentConfigError(
            "PORTAL_BFF_SIGNING_KEY_VERSION must be a fully qualified Cloud KMS key VERSION "
            "resource name, so the signing key stays non-exportable and pinned to one version"
        )


def load_deployment_config(env_file: Path, secrets_file: Path) -> DeploymentConfig:
    """Load and validate a named deployment from separate dotenv files."""

    values = load_env_file(env_file)
    if not secrets_file.is_file():
        raise DeploymentConfigError(f"required environment file does not exist: {secrets_file}")
    secret_mode = stat.S_IMODE(secrets_file.stat().st_mode)
    if secret_mode & 0o077:
        raise DeploymentConfigError(
            f"{secrets_file} must not be accessible by group or other users; use mode 0600"
        )
    secrets = load_env_file(secrets_file)
    _require_keys(values, REQUIRED_NONSECRET_KEYS, str(env_file))
    _require_keys(secrets, SECRET_KEYS, str(secrets_file))

    leaked = sorted(SECRET_KEYS & values.keys())
    if leaked:
        raise DeploymentConfigError(
            f"secret variables must not be in {env_file}: {', '.join(leaked)}"
        )
    unexpected_secrets = sorted(secrets.keys() - SECRET_KEYS - MODE5_SECRET_KEYS)
    if unexpected_secrets:
        raise DeploymentConfigError(
            f"non-secret variables must not be in {secrets_file}: {', '.join(unexpected_secrets)}"
        )
    unexpected_values = sorted(values.keys() - REQUIRED_NONSECRET_KEYS - OPTIONAL_NONSECRET_KEYS)
    if unexpected_values:
        raise DeploymentConfigError(
            f"unknown variables in {env_file}: {', '.join(unexpected_values)}"
        )

    for key in REQUIRED_NONSECRET_KEYS:
        _reject_placeholder(key, values[key], allow_empty=key == "DEPLOY_IAP_JWT_AUDIENCE")
    for key in SECRET_KEYS:
        _reject_placeholder(key, secrets[key])
    _validate_mode5_registration(values, secrets, env_file=env_file, secrets_file=secrets_file)

    project_id = values["GCP_PROJECT_ID"]
    if not _PROJECT_RE.fullmatch(project_id):
        raise DeploymentConfigError("GCP_PROJECT_ID is not a valid GCP project id")
    allowed_regions = _json_value(values, "GCP_ALLOWED_REGIONS_JSON", list)
    if not allowed_regions or not all(isinstance(region, str) for region in allowed_regions):
        raise DeploymentConfigError("GCP_ALLOWED_REGIONS_JSON must contain at least one region")
    if values["GCP_REGION"] not in allowed_regions:
        raise DeploymentConfigError("GCP_REGION must be in GCP_ALLOWED_REGIONS_JSON")
    # The region is a DEPLOY-TIME input validated against the allowlist above, which is the
    # same contract every other repository in this catalog uses. It previously required
    # literally asia-southeast1 and nothing else, which made this the one component that could
    # not follow a portfolio region decision without a code change — and it did not even match
    # the region the rest of the launch set settled on.
    #
    # What still has to hold is CO-LOCATION: the portal and every app it mounts deploy into
    # one region, because a cross-region hop between the portal and an embedded app crosses a
    # residency boundary and not merely a latency one. That is enforced by there being a
    # single GCP_REGION for the whole stack, and by each embedded app being deployed by this
    # same Terraform rather than pointed at somewhere else.
    if len(allowed_regions) != len(set(allowed_regions)):
        raise DeploymentConfigError("GCP_ALLOWED_REGIONS_JSON must not repeat a region")

    current_images = {
        "bff": values["DEPLOY_BFF_IMAGE"],
        "rm": values["DEPLOY_RM_SHELL_IMAGE"],
        "ops": values["DEPLOY_OPS_SHELL_IMAGE"],
    }
    _validate_images(current_images, "current_images")
    rollback_images = _json_value(values, "DEPLOY_ROLLBACK_IMAGES_JSON", dict)
    _validate_images(rollback_images, "DEPLOY_ROLLBACK_IMAGES_JSON")
    # Rollback must cover exactly what is BEING DEPLOYED, which is the property that actually
    # protects a release: a component with no recorded previous digest cannot be rolled back.
    # Deriving it from the deployed set rather than from a constant keeps that guarantee exact
    # for a partial deployment, instead of demanding rollback digests for apps this
    # installation does not run.
    deployed_apps = _json_value(values, "DEPLOY_EMBEDDED_APPS_JSON", dict)
    expected_rollback = set(current_images)
    for app_id in deployed_apps:
        expected_rollback.update({f"{app_id}-ui", f"{app_id}-api"})
    if set(rollback_images) != expected_rollback:
        missing_rollback = sorted(expected_rollback - rollback_images.keys())
        unexpected_rollback = sorted(rollback_images.keys() - expected_rollback)
        raise DeploymentConfigError(
            "DEPLOY_ROLLBACK_IMAGES_JSON must exactly cover every component; "
            f"missing={missing_rollback}, unexpected={unexpected_rollback}"
        )
    embedded_apps = deployed_apps
    _validate_embedded_apps(embedded_apps)

    rm_domain = values["DEPLOY_RM_DOMAIN"]
    ops_domain = values["DEPLOY_OPS_DOMAIN"]
    if not _DOMAIN_RE.fullmatch(rm_domain) or not _DOMAIN_RE.fullmatch(ops_domain):
        raise DeploymentConfigError("RM and Ops domains must be DNS hostnames")
    if rm_domain == ops_domain:
        raise DeploymentConfigError("RM and Ops domains must be distinct")
    tenant_id = values["DEPLOY_TENANT_ID"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", tenant_id):
        raise DeploymentConfigError("DEPLOY_TENANT_ID must be a stable lowercase identifier")
    # Which VERIFIED identity domains belong to that tenant. Required rather than optional: the
    # tenant id is a label the deployment chooses, the identity layer only ever sees a domain,
    # and leaving the two unrelated is what made a correctly configured deployment deny every
    # request. A deployment that really wants the old "tenant IS the domain" behaviour says so
    # by naming the domain and setting DEPLOY_TENANT_ID to it.
    identity_domains = _json_value(values, "DEPLOY_TENANT_IDENTITY_DOMAINS_JSON", list)
    if not identity_domains:
        raise DeploymentConfigError(
            "DEPLOY_TENANT_IDENTITY_DOMAINS_JSON must name at least one identity domain; with "
            "none, no verified identity resolves onto the tenant and every request is denied"
        )
    seen_domains: set[str] = set()
    for domain in identity_domains:
        if not isinstance(domain, str) or not _DOMAIN_RE.fullmatch(domain.strip().lower()):
            raise DeploymentConfigError(
                f"DEPLOY_TENANT_IDENTITY_DOMAINS_JSON must contain DNS domains, got {domain!r}"
            )
        if domain.strip().lower() in seen_domains:
            raise DeploymentConfigError(f"DEPLOY_TENANT_IDENTITY_DOMAINS_JSON repeats {domain!r}")
        seen_domains.add(domain.strip().lower())
    identity_domains = sorted(seen_domains)
    observability_url = values["DEPLOY_OBSERVABILITY_URL"].rstrip("/")
    observability_parsed = urlparse(observability_url)
    if not (
        observability_url == observability_url.lower()
        and observability_parsed.scheme == "https"
        and bool(observability_parsed.hostname)
        and bool(_DOMAIN_RE.fullmatch(observability_parsed.hostname or ""))
        and ".." not in (observability_parsed.hostname or "")
        and observability_parsed.port is None
        and observability_parsed.username is None
        and observability_parsed.password is None
        and observability_parsed.path == ""
        and not observability_parsed.params
        and not observability_parsed.query
        and not observability_parsed.fragment
    ):
        raise DeploymentConfigError(
            "DEPLOY_OBSERVABILITY_URL must be an exact lowercase HTTPS origin"
        )
    observability_audience = values["DEPLOY_OBSERVABILITY_AUDIENCE"].rstrip("/")
    audience_parsed = urlparse(observability_audience)
    if not (
        observability_audience == observability_audience.lower()
        and audience_parsed.scheme == "https"
        and bool(audience_parsed.hostname)
        and bool(_DOMAIN_RE.fullmatch(audience_parsed.hostname or ""))
        and ".." not in (audience_parsed.hostname or "")
        and audience_parsed.port is None
        and audience_parsed.username is None
        and audience_parsed.password is None
        and audience_parsed.path == ""
        and not audience_parsed.params
        and not audience_parsed.query
        and not audience_parsed.fragment
    ):
        raise DeploymentConfigError(
            "DEPLOY_OBSERVABILITY_AUDIENCE must be an exact lowercase HTTPS origin"
        )
    if values["DEPLOY_TLS_MODE"] != "google-managed":
        raise DeploymentConfigError("DEPLOY_TLS_MODE must be google-managed")
    audit_hmac_secret = values["DEPLOY_PORTAL_AUDIT_HMAC_SECRET"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", audit_hmac_secret):
        raise DeploymentConfigError("DEPLOY_PORTAL_AUDIT_HMAC_SECRET must be a Secret Manager name")
    audit_hmac_secret_version = values["DEPLOY_PORTAL_AUDIT_HMAC_SECRET_VERSION"]
    if not re.fullmatch(r"[1-9][0-9]*", audit_hmac_secret_version):
        raise DeploymentConfigError(
            "DEPLOY_PORTAL_AUDIT_HMAC_SECRET_VERSION must be an exact numeric version"
        )

    iap_members = _json_value(values, "DEPLOY_IAP_MEMBERS_JSON", list)
    if not all(
        isinstance(member, str)
        and re.fullmatch(r"(user|group|serviceAccount):[^\s]+@[^\s]+", member)
        for member in iap_members
    ):
        raise DeploymentConfigError(
            "DEPLOY_IAP_MEMBERS_JSON accepts explicit user:, group:, or serviceAccount: identities"
        )
    audience = values["DEPLOY_IAP_JWT_AUDIENCE"]
    if audience and not _AUDIENCE_RE.fullmatch(audience):
        raise DeploymentConfigError("DEPLOY_IAP_JWT_AUDIENCE is malformed")
    if iap_members and not audience:
        raise DeploymentConfigError("IAP members require the provider-computed IAP audience")

    frame_ancestors = _json_value(values, "DEPLOY_FRAME_ANCESTORS_JSON", list)
    cors_origins = _json_value(values, "DEPLOY_CORS_ORIGINS_JSON", list)

    def exact_https_origin(origin: Any) -> bool:
        if not isinstance(origin, str) or "*" in origin:
            return False
        parsed = urlparse(origin)
        try:
            _port = parsed.port
        except ValueError:
            return False
        return (
            origin == origin.lower()
            and parsed.scheme == "https"
            and bool(parsed.hostname)
            and bool(_DOMAIN_RE.fullmatch(parsed.hostname or ""))
            and ".." not in (parsed.hostname or "")
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path == ""
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        )

    if not frame_ancestors or not all(
        origin == "'self'" or exact_https_origin(origin) for origin in frame_ancestors
    ):
        raise DeploymentConfigError("frame ancestors must be exact HTTPS origins or 'self'")
    if not all(exact_https_origin(origin) for origin in cors_origins):
        raise DeploymentConfigError("CORS origins must be exact HTTPS origins")

    channels = _json_value(values, "DEPLOY_NOTIFICATION_CHANNELS_JSON", list)
    if not channels or not all(
        isinstance(channel, str) and _CHANNEL_RE.fullmatch(channel) for channel in channels
    ):
        raise DeploymentConfigError(
            "DEPLOY_NOTIFICATION_CHANNELS_JSON needs reviewed channel resources"
        )
    if len(channels) != len(set(channels)):
        raise DeploymentConfigError(
            "DEPLOY_NOTIFICATION_CHANNELS_JSON must contain distinct channel resources"
        )
    channel_prefix = f"projects/{project_id}/notificationChannels/"
    if not all(channel.startswith(channel_prefix) for channel in channels):
        raise DeploymentConfigError(
            "DEPLOY_NOTIFICATION_CHANNELS_JSON channels must belong to GCP_PROJECT_ID"
        )

    # `none` means the perimeter is owned by ANOTHER stack in this project.
    #
    # A GCP project may belong to exactly one regular service perimeter, and this catalog
    # deliberately co-locates several systems in one project ("one project holds the
    # portfolio"). Two stacks each creating their own perimeter over the same project is
    # therefore not a merge conflict, it is an API refusal: "A resource can be included in
    # exactly one regular service perimeter." Observed on the first real apply, 2026-08-24,
    # where the cdd-sow-research stack already had the project inside its perimeter.
    #
    # So a shared-project deployment names which stack owns the perimeter, rather than every
    # stack asserting one. The safety property is unchanged, because the perimeter still
    # covers the project either way; what changes is that only one stack manages it.
    access_policy_id = values["DEPLOY_VPC_SC_ACCESS_POLICY_ID"]
    if access_policy_id != "none" and not access_policy_id.isdigit():
        raise DeploymentConfigError(
            "DEPLOY_VPC_SC_ACCESS_POLICY_ID must be a numeric access-policy id, or 'none' "
            "when another stack in this project owns the perimeter"
        )
    if _boolean(values, "DEPLOY_VPC_SC_ENFORCED"):
        raise DeploymentConfigError(
            "DEPLOY_VPC_SC_ENFORCED must remain false until restricted egress is implemented"
        )
    state_bucket = values["DEPLOY_TERRAFORM_STATE_BUCKET"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]", state_bucket):
        raise DeploymentConfigError("DEPLOY_TERRAFORM_STATE_BUCKET is not a valid GCS bucket")
    state_prefix = values["DEPLOY_TERRAFORM_STATE_PREFIX"]
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}", state_prefix)
        or state_prefix.endswith("/")
        or any(part in {"", ".", ".."} for part in state_prefix.split("/"))
    ):
        raise DeploymentConfigError("DEPLOY_TERRAFORM_STATE_PREFIX is invalid")
    try:
        retention_days = int(values["DEPLOY_AUDIT_RETENTION_DAYS"])
    except ValueError as exc:
        raise DeploymentConfigError("DEPLOY_AUDIT_RETENTION_DAYS must be an integer") from exc
    if not 180 <= retention_days <= 3650:
        raise DeploymentConfigError("DEPLOY_AUDIT_RETENTION_DAYS must be between 180 and 3650")
    try:
        run_min_instances = int(values["DEPLOY_RUN_MIN_INSTANCES"])
    except ValueError as exc:
        raise DeploymentConfigError("DEPLOY_RUN_MIN_INSTANCES must be an integer") from exc
    if not 0 <= run_min_instances <= 100:
        raise DeploymentConfigError("DEPLOY_RUN_MIN_INSTANCES must be between 0 and 100")
    try:
        lb_log_sample_rate = float(values["DEPLOY_LB_LOG_SAMPLE_RATE"])
    except ValueError as exc:
        raise DeploymentConfigError("DEPLOY_LB_LOG_SAMPLE_RATE must be a number") from exc
    if not 0.0 <= lb_log_sample_rate <= 1.0:
        raise DeploymentConfigError("DEPLOY_LB_LOG_SAMPLE_RATE must be between 0 and 1")
    nat_log_filter = values["DEPLOY_NAT_LOG_FILTER"]
    if nat_log_filter not in _NAT_LOG_FILTERS:
        raise DeploymentConfigError(
            "DEPLOY_NAT_LOG_FILTER must be one of " + ", ".join(sorted(_NAT_LOG_FILTERS))
        )
    rotation_period = values["DEPLOY_CMEK_ROTATION_PERIOD"]
    if not _DURATION_RE.fullmatch(rotation_period):
        raise DeploymentConfigError(
            "DEPLOY_CMEK_ROTATION_PERIOD must be decimal seconds with up to 9 fractional digits"
        )
    try:
        rotation_seconds = Decimal(rotation_period[:-1])
    except InvalidOperation as exc:
        raise DeploymentConfigError("DEPLOY_CMEK_ROTATION_PERIOD is invalid") from exc
    if not _MIN_KMS_ROTATION_SECONDS <= rotation_seconds <= _MAX_KMS_ROTATION_SECONDS:
        raise DeploymentConfigError(
            "DEPLOY_CMEK_ROTATION_PERIOD must be between 86400s and 3153600000s"
        )
    for owner_key in _OWNER_KEYS:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", values[owner_key]):
            raise DeploymentConfigError(f"{owner_key} must name an accountable email owner")

    terraform_inputs: dict[str, Any] = {
        "project_id": project_id,
        "name_prefix": values["DEPLOY_NAME_PREFIX"],
        "region": values["GCP_REGION"],
        "allowed_regions": allowed_regions,
        "bff_image": current_images["bff"],
        "rm_shell_image": current_images["rm"],
        "ops_shell_image": current_images["ops"],
        "embedded_apps": embedded_apps,
        "rollback_images": rollback_images,
        "rm_domain": rm_domain,
        "ops_domain": ops_domain,
        # `none` is the explicit "resolved outside this deployment" statement — an
        # institution's existing zone, or a public wildcard resolver. Terraform already
        # supports that case: an empty dns_managed_zone emits the address without records.
        "dns_managed_zone": (
            "" if values["DEPLOY_DNS_MANAGED_ZONE"] == "none" else values["DEPLOY_DNS_MANAGED_ZONE"]
        ),
        "iap_oauth2_client_id": values["DEPLOY_IAP_OAUTH_CLIENT_ID"],
        "portal_audit_hmac_secret": audit_hmac_secret,
        "portal_audit_hmac_secret_version": audit_hmac_secret_version,
        "iap_jwt_audience": audience,
        "iap_members": iap_members,
        "frame_ancestors": frame_ancestors,
        "cors_origins": cors_origins,
        # Every reviewed identity domain maps onto THIS deployment's single tenant. The portal
        # is single-tenant per deployment, so a map is the right shape and one tenant is the
        # right value: several tenants behind one portal would need several embed policies and
        # several hosts, which is a different deployment, not a different mapping.
        "tenant_by_identity_domain": {domain: tenant_id for domain in identity_domains},
        "tenant_embed_policies": {
            f"{tenant_id}-primary": {
                "tenant": tenant_id,
                "hosts": [rm_domain, ops_domain],
                "frame_ancestors": frame_ancestors,
                "cors_origins": cors_origins,
            }
        },
        "observability_url": observability_url,
        "observability_audience": observability_audience,
        "notification_channels": channels,
        "apply_org_policies": _boolean(values, "DEPLOY_APPLY_ORG_POLICIES"),
        "vpc_sc_access_policy_id": ("" if access_policy_id == "none" else access_policy_id),
        "vpc_sc_enforced": _boolean(values, "DEPLOY_VPC_SC_ENFORCED"),
        "cmek_rotation_period": values["DEPLOY_CMEK_ROTATION_PERIOD"],
        "audit_retention_days": retention_days,
        "lock_audit_bucket": _boolean(values, "DEPLOY_LOCK_AUDIT_BUCKET"),
        "cloud_run_deletion_protection": _boolean(values, "DEPLOY_CLOUD_RUN_DELETION_PROTECTION"),
        "runtime_min_instances": run_min_instances,
        "lb_log_sample_rate": lb_log_sample_rate,
        "nat_log_filter": nat_log_filter,
        "production_edge_enabled": _boolean(values, "DEPLOY_PRODUCTION_EDGE_ENABLED"),
    }
    return DeploymentConfig(values=values, secrets=secrets, terraform_inputs=terraform_inputs)


def write_terraform_inputs(config: DeploymentConfig, output: Path) -> None:
    """Write only non-secret Terraform values to an ignored generated file."""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(config.terraform_inputs, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
