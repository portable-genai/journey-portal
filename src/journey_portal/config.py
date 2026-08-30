"""Settings + Container: profile-driven dependency injection (the hexagon wiring).

One env var (``PORTAL_PROFILE``) selects the adapter family for every port. ``local`` is the
SDK-free offline profile (dev / test / CI); ``gcp`` is the managed cloud stack (SDK imports stay
lazy so ``local`` / ``onprem`` import with no cloud SDK installed); ``onprem`` is the fail-fast
portability placeholder. The dotted ``module:Class`` binding table is the single source of truth.

``PORTAL_PROFILE`` is resolved here ONCE, by :func:`resolve_profile`, and an absent variable is
read as NO CHOICE rather than as consent to the ``local`` posture. This module is the only one
permitted to read it; ``tests/test_profile_single_source.py`` fails the build if any other module
re-derives it with its own permissive default, which is exactly how ``api/app.py`` came to carry
a second, unvalidated ``os.environ.get("PORTAL_PROFILE", "local")``.

The journey catalog (which apps, which journeys, their upstreams) is CONFIG, loaded and validated
from ``config/journeys.yaml`` with ``${ENV:-default}`` interpolation so the same file drives the
local launcher and a cloud deploy. Loading the catalog is pure once the raw mapping exists: the
domain :class:`JourneyCatalog` does the validation.
"""

from __future__ import annotations

import importlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from hex_service_kit.identity import IdentityPort
from hex_service_kit.netdefaults import EnvSetting, read_env_setting

from .domain.catalog import JourneyCatalog
from .domain.doc1_broker import Doc1BrokerPolicy
from .domain.embed_policy import TenantEmbedPolicyService
from .domain.models import TenantEmbedPolicy
from .envread import setting_or_default
from .ports.access_audit import AccessAuditPort
from .ports.bff_credentials import BffSigningKeyPort
from .ports.subject_token import SubjectTokenPort
from .ports.upstream import UpstreamClientPort

_PROFILE_ENV = "PORTAL_PROFILE"
_JOURNEYS_ENV = "PORTAL_JOURNEYS"
_UPSTREAM_TIMEOUT_ENV = "PORTAL_UPSTREAM_TIMEOUT"
_REGION_ENV = "PORTAL_REGION"
_IAP_AUDIENCE_ENV = "PORTAL_IAP_AUDIENCE"
_TENANT_DOMAINS_ENV = "PORTAL_TENANT_DOMAINS_JSON"
_AUDIT_HMAC_KEY_ENV = "PORTAL_AUDIT_HMAC_KEY"
_LOCAL_AUDIT_DB_ENV = "PORTAL_LOCAL_AUDIT_DB"
_LOCAL_AUDIT_KEY_FILE_ENV = "PORTAL_LOCAL_AUDIT_KEY_FILE"
_LOCAL_AUDIT_CHECKPOINT_ENV = "PORTAL_LOCAL_AUDIT_CHECKPOINT"
_TENANT_EMBED_POLICIES_ENV = "PORTAL_TENANT_EMBED_POLICIES_JSON"
_OBSERVABILITY_URL_ENV = "PORTAL_OBSERVABILITY_URL"
_OBSERVABILITY_AUDIENCE_ENV = "PORTAL_OBSERVABILITY_AUDIENCE"
# The Doc1 Mode 5 BFF service identity: the signing key, its published rotation window, and the
# reviewed broker registration. Every one of these is deployment-owned; none has a permissive
# default, and an unconfigured value makes the grant route refuse rather than guess.
_BFF_SIGNING_KEY_FILE_ENV = "PORTAL_BFF_SIGNING_KEY_FILE"
_BFF_SIGNING_KEY_VERSION_ENV = "PORTAL_BFF_SIGNING_KEY_VERSION"
_BFF_SIGNING_KID_ENV = "PORTAL_BFF_SIGNING_KID"
_BFF_ACCEPTED_JWKS_ENV = "PORTAL_BFF_ACCEPTED_PUBLIC_JWKS"
_BFF_CLIENT_ID_ENV = "PORTAL_DOC1_BFF_CLIENT_ID"
_DOC1_GRANT_ENDPOINT_ENV = "PORTAL_DOC1_GRANT_ENDPOINT"
_DOC1_INSTALLATION_ENV = "PORTAL_DOC1_INSTALLATION_ID"
_DOC1_SCOPES_ENV = "PORTAL_DOC1_REQUESTED_SCOPES"
_PUBLIC_ORIGIN_ENV = "PORTAL_PUBLIC_ORIGIN"
_SESSION_SIGNING_KEY_ENV = "PORTAL_SESSION_SIGNING_KEY"
#: The DEFAULT region when PORTAL_REGION is unset. It follows the portfolio region decision
#: (org-metadata docs/deployment-region-alignment.md, recorded 2026-08-23 and REVISED twice:
#: to us-central1 on 2026-08-24, and back to asia-southeast1 on 2026-08-27 once the deferred
#: per-service availability check was run). Terraform passes its own region and allowlist to
#: the runtime so the two cannot disagree; this default is what an unset deploy gets, and it
#: is not a residency recommendation. The reference deployment has NOT moved -- it still runs
#: in us-central1 and is overridden there at deploy time, which is why us-central1 remains a
#: member of DEFAULT_ALLOWED_REGIONS below.
REGION = "asia-southeast1"
_ALLOWED_REGIONS_ENV = "PORTAL_ALLOWED_REGIONS"
_DEPLOYED_APPS_ENV = "PORTAL_APPS"
#: The DEFAULT residency allowlist: the APAC regions this portal was built for, plus
#: us-central1, which stays a member for as long as the reference deployment runs there.
#: Removing it would make the running deployment fail its own residency validation. It is a
#: default, never
#: a ceiling. Residency is a deploy-time decision in every other repository in
#: this catalog — Doc1 takes DOC1_ALLOWED_REGIONS — and hardcoding a fixed set here made the
#: portal the one component that could not follow a portfolio region decision without a code
#: change, which is precisely the coupling the portability thesis argues against.
DEFAULT_ALLOWED_REGIONS = frozenset(
    {
        "us-central1",
        "asia-southeast1",
        "australia-southeast1",
        "australia-southeast2",
        "asia-east2",
        "asia-northeast1",
    }
)
#: Kept as a module-level name because callers and tests import it. It is the DEFAULT set;
#: resolve_allowed_regions() is what a deployment actually gets.
ALLOWED_REGIONS = DEFAULT_ALLOWED_REGIONS


def resolve_allowed_regions() -> frozenset[str]:
    """The residency allowlist for this deployment.

    PORTAL_ALLOWED_REGIONS overrides the default as a comma-separated list. Set-but-empty is
    an ERROR rather than "allow everything": an empty allowlist would turn the residency
    control off silently, and a control that can be disabled by a typo is not a control.
    """
    setting = read_env_setting(_ALLOWED_REGIONS_ENV)
    if setting.is_configured_empty:
        raise ValueError(
            f"{_ALLOWED_REGIONS_ENV} is set but empty; unset it to use the default allowlist, "
            "or list the approved regions"
        )
    if not setting.value:
        return DEFAULT_ALLOWED_REGIONS
    regions = frozenset(part.strip() for part in setting.value.split(",") if part.strip())
    if not regions:
        raise ValueError(f"{_ALLOWED_REGIONS_ENV} must list at least one approved region")
    return regions


PROFILES = frozenset({"local", "gcp", "platform", "onprem"})

#: The profile string handed to every INTERNET-FACING posture decision when ``PORTAL_PROFILE``
#: was never set. Deliberately NOT a member of :data:`PROFILES` and never reaching
#: :class:`Settings`: it exists so that "no choice was made" is a distinct input to the security
#: layers rather than being indistinguishable from a chosen ``local``.
UNCONSENTED_PROFILE = "unconfigured"
_DEFAULT_JOURNEYS = "config/journeys.yaml"
_DEFAULT_UPSTREAM_TIMEOUT = 30.0
_DEFAULT_LOCAL_AUDIT_DB = ".local/portal-access-audit.sqlite3"
_DEFAULT_BFF_SIGNING_KEY_FILE = ".local/portal-bff-signing-key.json"
#: The offline demo's own values for the Doc1 brokered grant. Granted ONLY to a deliberately
#: chosen ``local`` profile (never to an unconsented run), because they are a relaxation: they
#: let the grant route run end to end with a fictional registration and no reviewed policy.
_LOCAL_DOC1_GRANT_ENDPOINT = "http://127.0.0.1:8090/v1/embed/grants"
_LOCAL_DOC1_INSTALLATION = "inst_local_demo"
_LOCAL_BFF_CLIENT_ID = "hrz9-journey-portal-bff-local-demo"
_LOCAL_PUBLIC_ORIGIN = "http://127.0.0.1:8110"
_DEFAULT_DOC1_SCOPES = ("cdd.embed", "cdd.read")
# One entry per journey shell the local demo serves, on both loopback spellings. A shell
# whose origin is missing here is refused by the tenant embedding policy, and the refusal
# reaches the browser as a 403 on the embedded app's own assets rather than as a message
# about origins, so the app renders its chrome and then reports its backend unreachable.
# Ports: rm 3000, mkt 3001, gov 3002, svc 3003, ops 4200 (see scripts/run_journeys.py).
_LOCAL_CORS_ORIGINS = tuple(
    f"http://{host}:{port}"
    for port in (3000, 3001, 3002, 3003, 4200)
    for host in ("localhost", "127.0.0.1")
)

# ``${VAR}`` or ``${VAR:-default}`` interpolation over config strings.
_INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# port -> profile -> "module:Class". Every port needs a local and an onprem binding (the parity
# test asserts it); gcp is the managed default fallen back to for an unknown profile.
_BINDINGS: dict[str, dict[str, str]] = {
    "access_audit": {
        "local": "journey_portal.adapters.local.access_audit:LocalAccessAuditAdapter",
        "gcp": "journey_portal.adapters.gcp.access_audit:GcpAccessAuditAdapter",
        "platform": ("journey_portal.adapters.platform.access_audit:PlatformAccessAuditAdapter"),
        "onprem": "journey_portal.adapters.onprem.access_audit:OnPremAccessAuditAdapter",
    },
    "identity": {
        "local": "journey_portal.adapters.local.identity:LocalIdentityAdapter",
        "gcp": "journey_portal.adapters.gcp.identity:IapIdentityAdapter",
        "platform": "journey_portal.adapters.platform.identity:PlatformIdentityAdapter",
        "onprem": "journey_portal.adapters.onprem.identity:OnPremIdentityAdapter",
    },
    "upstream": {
        "local": "journey_portal.adapters.local.upstream:HttpxUpstreamClient",
        "gcp": "journey_portal.adapters.gcp.upstream:GcpUpstreamClient",
        "platform": "journey_portal.adapters.platform.upstream:PlatformUpstreamClient",
        "onprem": "journey_portal.adapters.onprem.upstream:OnPremUpstreamClient",
    },
    "bff_signing_key": {
        "local": ("journey_portal.adapters.local.bff_credentials:LocalBffSigningKeyAdapter"),
        "gcp": "journey_portal.adapters.gcp.bff_credentials:KmsBffSigningKeyAdapter",
        "platform": (
            "journey_portal.adapters.platform.bff_credentials:PlatformBffSigningKeyAdapter"
        ),
        "onprem": ("journey_portal.adapters.onprem.bff_credentials:OnPremBffSigningKeyAdapter"),
    },
    "subject_token": {
        "local": "journey_portal.adapters.local.subject_token:LocalSubjectTokenAdapter",
        "gcp": ("journey_portal.adapters.gcp.subject_token:PendingGoogleSubjectTokenAdapter"),
        "platform": ("journey_portal.adapters.platform.subject_token:PlatformSubjectTokenAdapter"),
        "onprem": "journey_portal.adapters.onprem.subject_token:OnPremSubjectTokenAdapter",
    },
}


class ProfileNotConfigured(ValueError):
    """No ``PORTAL_PROFILE`` was chosen, so a posture decision has no consented answer.

    Raised while loading settings, before any request is served and before any credential is
    inspected, so the refusal needs no cloud SDK and cannot be reached by a caller.
    """


#: The profiles whose runtime is a managed cloud, for :attr:`Settings.runtime`. ``onprem`` is
#: NOT one -- running on the adopter's own iron is its entire point, and "on GCP" is the one
#: sentence that deployment must never print at the top of a page.
_MANAGED_PROFILES: frozenset[str] = frozenset({"gcp", "platform"})


@dataclass(frozen=True, slots=True)
class ProfileChoice:
    """The ONE resolution of ``PORTAL_PROFILE``, and what each consumer must key off.

    The two derived profile strings differ because the two kinds of decision fail closed in
    OPPOSITE directions, so a single "effective profile" string would harden one and weaken the
    other: a RELAXATION keyed off ``local`` (the localhost CORS origins, the wildcard tenant
    embed policy, the persona selector, no HSTS, the injected ``X-Dev-Persona``) must not be
    granted to an unconsented run, while the bind RESTRICTION treats ``local`` as the confined
    case and must be applied to it.
    """

    #: Which adapter family to bind. Absent consent this is still ``local`` (the SDK-free
    #: adapters), because the alternative would import cloud SDKs that are not installed.
    profile: str = "local"
    #: Was the profile named DELIBERATELY (``PORTAL_PROFILE`` present and non-blank)?
    explicit: bool = True

    @property
    def exposure_profile(self) -> str:
        """The profile every *relaxation* keys off; :data:`UNCONSENTED_PROFILE` when unset."""
        return self.profile if self.explicit else UNCONSENTED_PROFILE

    @property
    def bind_profile(self) -> str:
        """The profile the bind guard keys off, where ``local`` is the RESTRICTIVE case."""
        return self.profile if self.explicit else "local"


def resolve_profile(environ: Mapping[str, str] | None = None) -> ProfileChoice:
    """Read ``PORTAL_PROFILE`` once, in THREE states, treating absent as NO CHOICE not ``local``.

    The read goes through the commons :func:`~hex_service_kit.netdefaults.read_env_setting` so the
    three states stay distinct (the same doctrine the bind guard uses):

    * UNSET - no intent was expressed, so the SDK-free ``local`` adapters are inherited but
      ``explicit`` is ``False``: every relaxation downstream keys off :data:`UNCONSENTED_PROFILE`,
      not ``local``.
    * SET-AND-EMPTY (``PORTAL_PROFILE=`` or whitespace) - an intent WAS expressed and it names no
      profile, so it fails closed as a boot failure rather than collapsing into the unset default;
      this is the case ``bool(raw)`` used to erase.
    * SET WITH A VALUE - validated here, exact and case-sensitive on purpose: every posture
      decision downstream matches the profile string exactly, so ``Local`` would select none of
      the relaxations but also none of the restrictions. Normalising the case would turn a typo
      into a silent choice; refusing it turns the typo into a load failure.
    """
    if environ is None:
        setting = read_env_setting(_PROFILE_ENV)
    else:
        raw = environ.get(_PROFILE_ENV)
        setting = EnvSetting(name=_PROFILE_ENV, raw=raw, value="" if raw is None else raw.strip())
    if setting.is_configured_empty:
        raise ValueError(
            f"{_PROFILE_ENV} is set but empty; unset it to inherit the offline local posture, "
            f"or name one of {sorted(PROFILES)}"
        )
    if setting.has_value and setting.value not in PROFILES:
        raise ValueError(f"{_PROFILE_ENV} must be one of {sorted(PROFILES)}, got {setting.value!r}")
    return ProfileChoice(profile=setting.value or "local", explicit=setting.has_value)


def _interpolate(value: Any) -> Any:
    """Recursively substitute ``${ENV:-default}`` in every string leaf of a loaded config.

    ``${ENV:-default}`` is ``setting_or_default(name, default)`` one layer down, so it delegates
    to that helper rather than restating the rule: unset takes the written default, a value wins,
    and a variable an operator EMPTIED raises ``ConfiguredEmptyError`` instead of resolving to the
    empty string, which for an upstream URL or an origin allowlist is the permissive branch.
    """
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            return setting_or_default(match.group(1), match.group(2) or "")

        return _INTERP.sub(replace, value)
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value


def _upstream_timeout_seconds() -> float:
    """Read ``PORTAL_UPSTREAM_TIMEOUT``, failing closed on anything but a positive number."""
    setting = read_env_setting(_UPSTREAM_TIMEOUT_ENV)
    if setting.is_unset:
        return _DEFAULT_UPSTREAM_TIMEOUT
    if setting.is_configured_empty:
        raise ValueError(
            f"{_UPSTREAM_TIMEOUT_ENV} is set but empty; unset it to use "
            f"{_DEFAULT_UPSTREAM_TIMEOUT:g} seconds, or provide a positive number"
        )
    raw = setting.value
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_UPSTREAM_TIMEOUT_ENV} must be a number of seconds, got {raw!r}"
        ) from exc
    if seconds <= 0:
        raise ValueError(f"{_UPSTREAM_TIMEOUT_ENV} must be greater than zero, got {raw!r}")
    return seconds


def _local_tenant_embed_policies() -> tuple[TenantEmbedPolicy, ...]:
    """The no-IdP demo accepts both seeded tenants on loopback and the test host."""
    return (
        TenantEmbedPolicy(
            policy_id="local-demo",
            tenant="*",
            hosts=("127.0.0.1", "localhost", "testserver"),
            frame_ancestors=("'self'",),
            cors_origins=_LOCAL_CORS_ORIGINS,
        ),
    )


def _tenant_embed_policies(exposure_profile: str) -> tuple[TenantEmbedPolicy, ...]:
    """The reviewed tenant embed registry; an EMPTY one refuses rather than opening up.

    ``exposure_profile``, not the raw profile: the seeded fallback carries the wildcard tenant
    ``*``, the single most permissive object in this repo, and it is granted only to a profile
    somebody chose. An unconsented run reaches the same refusal an unconfigured secure deploy
    does, which is why the registry cannot silently be empty.
    """
    setting = read_env_setting(_TENANT_EMBED_POLICIES_ENV)
    if setting.is_configured_empty:
        raise ValueError(
            f"{_TENANT_EMBED_POLICIES_ENV} is set but empty; unset it only for the deliberate "
            "local demo fallback, or provide the reviewed non-empty registry"
        )
    raw = setting.value
    if setting.is_unset:
        if exposure_profile == "local":
            return _local_tenant_embed_policies()
        if exposure_profile == UNCONSENTED_PROFILE:
            raise ProfileNotConfigured(
                f"{_PROFILE_ENV} is not set, so the local profile was inherited rather than "
                f"chosen, and the seeded wildcard tenant embed policy is refused. Set "
                f"{_PROFILE_ENV}=local deliberately for a dev or demo run, or set "
                f"{_TENANT_EMBED_POLICIES_ENV} to the reviewed registry for a real deployment."
            )
        raise ValueError(f"{_TENANT_EMBED_POLICIES_ENV} is required outside the local profile")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{_TENANT_EMBED_POLICIES_ENV} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        document = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_TENANT_EMBED_POLICIES_ENV} must contain valid JSON") from exc
    if not isinstance(document, dict) or not document:
        raise ValueError(f"{_TENANT_EMBED_POLICIES_ENV} must be a non-empty JSON object")

    policies: list[TenantEmbedPolicy] = []
    expected_fields = {"tenant", "hosts", "frame_ancestors", "cors_origins"}
    for policy_id, value in document.items():
        if not isinstance(policy_id, str) or not isinstance(value, dict):
            raise ValueError(f"{_TENANT_EMBED_POLICIES_ENV} must map policy ids to JSON objects")
        if set(value) != expected_fields:
            raise ValueError(
                f"{_TENANT_EMBED_POLICIES_ENV}.{policy_id} must contain exactly "
                f"{sorted(expected_fields)}"
            )
        if not isinstance(value["tenant"], str) or not all(
            isinstance(value[field], list) and all(isinstance(item, str) for item in value[field])
            for field in ("hosts", "frame_ancestors", "cors_origins")
        ):
            raise ValueError(
                f"{_TENANT_EMBED_POLICIES_ENV}.{policy_id} contains invalid field types"
            )
        policies.append(
            TenantEmbedPolicy(
                policy_id=policy_id,
                tenant=value["tenant"],
                hosts=tuple(value["hosts"]),
                frame_ancestors=tuple(value["frame_ancestors"]),
                cors_origins=tuple(value["cors_origins"]),
            )
        )
    return TenantEmbedPolicyService(
        tuple(policies),
        allow_local_wildcard=exposure_profile == "local",
    ).policies


def _tenant_by_domain() -> Mapping[str, str]:
    """Parse the reviewed identity-domain -> tenant map, refusing anything half-configured."""

    raw = _optional_setting(_TENANT_DOMAINS_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{_TENANT_DOMAINS_ENV} must be a JSON object") from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError(f"{_TENANT_DOMAINS_ENV} must be a non-empty JSON object")
    mapping: dict[str, str] = {}
    for domain, tenant in parsed.items():
        if not isinstance(domain, str) or not isinstance(tenant, str):
            raise ValueError(f"{_TENANT_DOMAINS_ENV} must map domain strings to tenant strings")
        domain_key = domain.strip().lower()
        tenant_value = tenant.strip()
        if not domain_key or not tenant_value:
            raise ValueError(
                f"{_TENANT_DOMAINS_ENV} contains an empty domain or tenant. A blank key would "
                "map the identities that present NO domain onto a real tenant, which is the one "
                "entry that must be written deliberately rather than by accident."
            )
        mapping[domain_key] = tenant_value
    return mapping


def _optional_setting(name: str) -> str:
    """Return an optional value while rejecting an explicitly empty configuration entry."""
    setting = read_env_setting(name)
    if setting.is_configured_empty:
        raise ValueError(
            f"{name} is set but empty; unset it when the capability is intentionally absent, "
            "or provide a non-empty value"
        )
    return setting.value


def _defaulted_setting(name: str, default: str) -> str:
    """Apply ``default`` only when ``name`` is absent, never when it was deliberately emptied."""
    setting = read_env_setting(name)
    if setting.is_configured_empty:
        raise ValueError(
            f"{name} is set but empty; unset it to use {default!r}, or provide a non-empty value"
        )
    return setting.value or default


def _demo_default(name: str, exposure_profile: str, local_default: str) -> str:
    """Read one Doc1 broker setting, granting the demo fallback only to a CHOSEN local profile.

    The fallbacks are fictional registration values that let the offline demo run the grant path
    end to end. They are a relaxation, so they key off the exposure profile: an unconsented run
    and every non-local profile get an empty string, and the grant route then refuses with a
    message naming this variable. A variable that IS set always wins, in every profile.
    """
    setting = read_env_setting(name)
    if setting.is_configured_empty:
        raise ValueError(
            f"{name} is set but empty; unset it only for the deliberate local demo fallback, "
            "or provide the reviewed deployment value"
        )
    if setting.has_value:
        return setting.value
    return local_default if exposure_profile == "local" else ""


def _requested_scopes() -> tuple[str, ...]:
    """Read the comma-separated grant scopes; an EMPTIED variable refuses rather than defaults."""
    setting = read_env_setting(_DOC1_SCOPES_ENV)
    if setting.is_unset:
        return _DEFAULT_DOC1_SCOPES
    scopes = tuple(item.strip() for item in setting.value.split(",") if item.strip())
    if not scopes:
        raise ValueError(
            f"{_DOC1_SCOPES_ENV} is set but names no scope; unset it for the reviewed default "
            f"{','.join(_DEFAULT_DOC1_SCOPES)} or list the scopes this installation may request"
        )
    return scopes


#: One ephemeral CSRF/session-binding secret per process, minted lazily for the local profile.
_EPHEMERAL_SESSION_KEY = ""


def _session_signing_key(exposure_profile: str) -> str:
    """Resolve the CSRF and session-binding secret; only a CHOSEN local run may improvise one.

    Outside ``local`` the secret must come from the deployment (Secret Manager), and an unset
    variable leaves it empty so the CSRF routes refuse. Inside a deliberately chosen ``local``
    run the portal mints one ephemeral per-process secret instead, so the offline demo works
    without a secret store; CSRF tokens then do not survive a restart, which is correct for a
    dev affordance and would be unacceptable for a deployment.
    """
    global _EPHEMERAL_SESSION_KEY
    setting = read_env_setting(_SESSION_SIGNING_KEY_ENV)
    if setting.is_configured_empty:
        raise ValueError(
            f"{_SESSION_SIGNING_KEY_ENV} is set but empty; unset it only for the deliberate "
            "local ephemeral key, or provide a deployment secret"
        )
    if setting.has_value:
        return setting.value
    if exposure_profile != "local":
        return ""
    if not _EPHEMERAL_SESSION_KEY:
        _EPHEMERAL_SESSION_KEY = secrets.token_urlsafe(32)
    return _EPHEMERAL_SESSION_KEY


def load_journeys_mapping(path: str | Path) -> dict[str, Any]:
    """Read and env-interpolate the journeys YAML into a raw mapping (no validation yet)."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"journeys config not found: {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    interpolated = _interpolate(raw)
    if not isinstance(interpolated, dict):
        raise TypeError(f"journeys config must be a mapping, got {type(interpolated).__name__}")
    return interpolated


@dataclass(frozen=True, slots=True)
class Settings:
    """Deployment settings, resolved from the environment."""

    profile: str = "local"
    region: str = REGION
    #: The apps this installation actually mounts, or None for "everything in the config".
    #: See JourneyCatalog.from_mapping for why a deployment states this rather than the
    #: portal inferring it from which upstreams happen to look configured.
    deployed_apps: frozenset[str] | None = None
    journeys_path: str = _DEFAULT_JOURNEYS
    # Configurable because an upstream's work is not always fast: an embedded app running a real
    # (rather than offline-deterministic) profile can take minutes to answer one request, and the
    # 30s default would kill it at the proxy. Raise the configured upstream-timeout env for that.
    upstream_timeout_seconds: float = _DEFAULT_UPSTREAM_TIMEOUT
    persona_cookie: str = "portal_persona"
    iap_audience: str = ""
    local_audit_db: str = _DEFAULT_LOCAL_AUDIT_DB
    audit_hmac_key: str = ""
    local_audit_key_file: str = ""
    local_audit_checkpoint: str = ""
    tenant_embed_policies: tuple[TenantEmbedPolicy, ...] = ()
    #: Verified identity domain -> reviewed tenant id. The managed identity adapter derives the
    #: tenant from the assertion's hosted domain, while the tenant embed registry is keyed by the
    #: deployment's own tenant LABEL, and the two are not the same string: a deployment for
    #: "reference-bank" is signed into by people whose Workspace domain is something else
    #: entirely. With no mapping the host check compared a Workspace domain against a tenant
    #: label, never matched, and denied every request on a correctly configured deployment.
    #: Empty means "the tenant IS the domain", which is the old behaviour and stays valid.
    tenant_by_domain: Mapping[str, str] = field(default_factory=dict)
    observability_url: str = ""
    observability_audience: str = ""
    # --- Doc1 Mode 5 BFF service identity and brokered-grant registration ---------------
    # Key custody: a gitignored local file for the offline profile, a Cloud KMS key VERSION for
    # the managed ones. The private key is never a setting, in either shape.
    bff_signing_key_file: str = _DEFAULT_BFF_SIGNING_KEY_FILE
    bff_signing_key_version: str = ""
    bff_signing_kid: str = ""
    bff_accepted_public_jwks: str = ""
    bff_client_id: str = ""
    doc1_grant_endpoint: str = ""
    doc1_installation_id: str = ""
    doc1_requested_scopes: tuple[str, ...] = _DEFAULT_DOC1_SCOPES
    public_origin: str = ""
    session_signing_key: str = ""
    # Was the profile chosen DELIBERATELY, or merely inherited from the fallback? ``load`` sets
    # this False when PORTAL_PROFILE is absent; configured-empty refuses. Direct construction is
    # definition (a caller named the profile in code), so the default is True.
    profile_explicit: bool = True

    @property
    def runtime(self) -> str:
        """Where this portal is running, as its banner states it: ``gcp`` or ``local``.

        Derived from the profile, never sniffed from the environment. A shell that read
        its runtime from ``window.location`` would be right until the deployment served
        through a proxy and wrong silently after that, so the service is the one asked.
        """
        return "gcp" if self.profile in _MANAGED_PROFILES else "local"

    @property
    def generator_model(self) -> str:
        """Which model answers, for the provenance banner (org decision, 2026-08-30).

        None does. The portal is a launcher and a BFF: it mounts embedded apps, brokers
        their identity and proxies their calls, and it declares no ``llm`` port anywhere.
        Every page that shows GENERATED content is an embedded app, and each of those
        states its own provenance from its own healthz, inside its own frame.

        That is why the portal's banner is worth having rather than redundant. The two
        answers can legitimately differ -- a portal on GCP mounting an app on a laptop, or
        the reverse -- and a viewer reading a dossier in a frame needs both facts, not
        whichever one happened to be rendered. ``no-model`` is the honest string for a
        surface that generates nothing, and it is deliberately not
        ``deterministic-offline-stub``, which would claim a model-shaped port bound to a
        stub.
        """
        return "no-model"

    @property
    def choice(self) -> ProfileChoice:
        """The resolved profile, split into the relaxation and restriction views."""
        return ProfileChoice(profile=self.profile, explicit=self.profile_explicit)

    @property
    def doc1_broker_policy(self) -> Doc1BrokerPolicy:
        """The reviewed Doc1 brokered-grant registration; raises when it is unconfigured.

        Every member is deployment-owned, so an incomplete registration is a refusal rather than
        a default: the alternative is a portal that authenticates to some endpoint under some
        client id nobody reviewed. :class:`Doc1BrokerPolicy` raises ``BrokerPolicyError`` on any
        blank member, and the grant route turns that into a 503 naming the missing variable.
        """
        return Doc1BrokerPolicy(
            grant_endpoint=self.doc1_grant_endpoint,
            installation_id=self.doc1_installation_id,
            bff_client_id=self.bff_client_id,
            portal_origin=self.public_origin,
            requested_scopes=self.doc1_requested_scopes,
        )

    @classmethod
    def load(cls) -> Settings:
        choice = resolve_profile()
        profile = choice.profile
        region_setting = read_env_setting(_REGION_ENV)
        if region_setting.is_configured_empty:
            raise ValueError(
                f"{_REGION_ENV} is set but empty; unset it to use {REGION}, or provide an "
                "approved region"
            )
        deployed_apps_setting = read_env_setting(_DEPLOYED_APPS_ENV)
        if deployed_apps_setting.is_configured_empty:
            raise ValueError(
                f"{_DEPLOYED_APPS_ENV} is set but empty; unset it to serve every app in the "
                "journeys config, or list the apps this deployment mounts"
            )
        deployed_apps = (
            frozenset(
                part.strip() for part in deployed_apps_setting.value.split(",") if part.strip()
            )
            if deployed_apps_setting.value
            else None
        )
        allowed_regions = resolve_allowed_regions()
        region = region_setting.value or REGION
        if region not in allowed_regions:
            raise ValueError(
                f"{_REGION_ENV} must be one of {sorted(allowed_regions)}, got {region!r}"
            )
        observability_url = _optional_setting(_OBSERVABILITY_URL_ENV)
        observability_audience = _optional_setting(_OBSERVABILITY_AUDIENCE_ENV)
        if profile == "platform" and not observability_url:
            raise ValueError(f"{_OBSERVABILITY_URL_ENV} is required for the platform profile")
        if profile == "platform" and not observability_audience:
            raise ValueError(f"{_OBSERVABILITY_AUDIENCE_ENV} is required for the platform profile")
        return cls(
            profile=profile,
            region=region,
            deployed_apps=deployed_apps,
            journeys_path=_defaulted_setting(_JOURNEYS_ENV, _DEFAULT_JOURNEYS),
            upstream_timeout_seconds=_upstream_timeout_seconds(),
            iap_audience=_optional_setting(_IAP_AUDIENCE_ENV),
            tenant_by_domain=_tenant_by_domain(),
            audit_hmac_key=_optional_setting(_AUDIT_HMAC_KEY_ENV),
            local_audit_db=_defaulted_setting(_LOCAL_AUDIT_DB_ENV, _DEFAULT_LOCAL_AUDIT_DB),
            local_audit_key_file=_optional_setting(_LOCAL_AUDIT_KEY_FILE_ENV),
            local_audit_checkpoint=_optional_setting(_LOCAL_AUDIT_CHECKPOINT_ENV),
            tenant_embed_policies=_tenant_embed_policies(choice.exposure_profile),
            observability_url=observability_url,
            observability_audience=observability_audience,
            profile_explicit=choice.explicit,
            bff_signing_key_file=_defaulted_setting(
                _BFF_SIGNING_KEY_FILE_ENV, _DEFAULT_BFF_SIGNING_KEY_FILE
            ),
            bff_signing_key_version=_optional_setting(_BFF_SIGNING_KEY_VERSION_ENV),
            bff_signing_kid=_optional_setting(_BFF_SIGNING_KID_ENV),
            bff_accepted_public_jwks=_optional_setting(_BFF_ACCEPTED_JWKS_ENV),
            bff_client_id=_demo_default(
                _BFF_CLIENT_ID_ENV, choice.exposure_profile, _LOCAL_BFF_CLIENT_ID
            ),
            doc1_grant_endpoint=_demo_default(
                _DOC1_GRANT_ENDPOINT_ENV, choice.exposure_profile, _LOCAL_DOC1_GRANT_ENDPOINT
            ),
            doc1_installation_id=_demo_default(
                _DOC1_INSTALLATION_ENV, choice.exposure_profile, _LOCAL_DOC1_INSTALLATION
            ),
            doc1_requested_scopes=_requested_scopes(),
            public_origin=_demo_default(
                _PUBLIC_ORIGIN_ENV, choice.exposure_profile, _LOCAL_PUBLIC_ORIGIN
            ),
            session_signing_key=_session_signing_key(choice.exposure_profile),
        )


class Container:
    """Lazy DI container: one ``cached_property`` per port, bound by the active profile."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _bind(self, port: str) -> object:
        table = _BINDINGS[port]
        try:
            target = table[self.settings.profile]
        except KeyError as exc:
            raise ValueError(
                f"unsupported profile {self.settings.profile!r}; expected one of {sorted(table)}"
            ) from exc
        module_path, _, cls_name = target.partition(":")
        adapter_cls = getattr(importlib.import_module(module_path), cls_name)
        return adapter_cls(self.settings)

    @cached_property
    def identity(self) -> IdentityPort:
        adapter = self._bind("identity")
        assert isinstance(adapter, IdentityPort)
        return adapter

    @cached_property
    def access_audit(self) -> AccessAuditPort:
        adapter = self._bind("access_audit")
        assert isinstance(adapter, AccessAuditPort)
        return adapter

    @cached_property
    def embed_policy(self) -> TenantEmbedPolicyService:
        # The seeded wildcard fallback and the wildcard PERMISSION both key off the exposure
        # profile, so a Settings built with an unconsented profile cannot get here and pick up
        # the relaxation that ``Settings.load`` just refused.
        exposure = self.settings.choice.exposure_profile
        policies = self.settings.tenant_embed_policies
        if not policies and exposure == "local":
            policies = _local_tenant_embed_policies()
        return TenantEmbedPolicyService(
            policies,
            allow_local_wildcard=exposure == "local",
        )

    @cached_property
    def upstream(self) -> UpstreamClientPort:
        adapter = self._bind("upstream")
        assert isinstance(adapter, UpstreamClientPort)
        return adapter

    @cached_property
    def bff_signing_key(self) -> BffSigningKeyPort:
        adapter = self._bind("bff_signing_key")
        assert isinstance(adapter, BffSigningKeyPort)
        return adapter

    @cached_property
    def subject_token(self) -> SubjectTokenPort:
        adapter = self._bind("subject_token")
        assert isinstance(adapter, SubjectTokenPort)
        return adapter

    @cached_property
    def catalog(self) -> JourneyCatalog:
        catalog = JourneyCatalog.from_mapping(
            load_journeys_mapping(self.settings.journeys_path),
            only_apps=self.settings.deployed_apps,
        )
        catalog.validate_for_profile(self.settings.profile)
        return catalog


def build_container(settings: Settings | None = None) -> Container:
    return Container(settings or Settings.load())


def identity_adapter_class(profile: str) -> type:
    """The identity adapter CLASS the active binding names, resolved WITHOUT constructing it.

    Reads the SAME :data:`_BINDINGS` table :meth:`Container._bind` binds from, so a deployment
    that rebound the identity port (the documented on-premises path: swap the placeholder for
    the client's own IdP adapter, ``docs/onprem-migration.md``) is answered about the adapter it
    ACTUALLY runs, not about the one the profile name suggests.

    Takes the PROFILE STRING rather than a ``Settings``: the answer is needed at IMPORT, on the
    app object, and ``Settings.load()`` raises for exactly the unconfigured run whose posture the
    exposure guard most needs to know. Constructing the adapter is avoided for the same reason
    twice over: the seeded-persona adapter REFUSES to construct under an inherited profile, so a
    posture computed from an instance would be unobtainable in one of the cases it must describe.
    """
    table = _BINDINGS["identity"]
    try:
        target = table[profile]
    except KeyError as exc:
        raise ValueError(
            f"unsupported profile {profile!r}; expected one of {sorted(table)}"
        ) from exc
    module_path, _, cls_name = target.partition(":")
    resolved = getattr(importlib.import_module(module_path), cls_name)
    if not isinstance(resolved, type):
        raise TypeError(f"identity binding {target!r} does not name a class")
    return resolved


def end_user_auth_kind(profile: str) -> str:
    """What the BOUND identity adapter declares it does for end-user authentication.

    This is the one question "is this portal serving an authenticated end user?" reduces to.
    See :mod:`journey_portal.ports.identity` for why neither the profile string on its own, nor
    a service credential, nor the host-header tenant check can answer it.

    Any failure to establish the answer resolves to
    :data:`~journey_portal.ports.identity.CLIENT_ASSERTED`. A guard that switches OFF because a
    lookup raised is a guard that fails open, and nothing is lost by failing closed here: the
    same failure surfaces loudly at the first request, when the container resolves the identical
    binding for real.
    """
    from .ports.identity import CLIENT_ASSERTED, declared_end_user_auth

    try:
        return declared_end_user_auth(identity_adapter_class(profile))
    except Exception:  # noqa: BLE001 - a guard that fails open on a lookup error is no guard
        return CLIENT_ASSERTED
