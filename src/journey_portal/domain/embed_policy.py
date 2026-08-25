"""Pure deterministic tenant framing and CORS policy evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import (
    EmbedPolicyFinding,
    EmbedPolicyFindingKind,
    EmbedPolicySeverity,
    TenantEmbedAssessment,
    TenantEmbedPolicy,
)

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


def _canonical_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or not _HOST.fullmatch(host) or ".." in host:
        raise ValueError(f"tenant embed policy host is invalid: {value!r}")
    return host


def _exact_origin(value: str, *, allow_loopback_http: bool) -> str:
    origin = value.strip()
    if "*" in origin or origin != origin.lower():
        raise ValueError("tenant embed policy origins must be lowercase and wildcard-free")
    parsed = urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"tenant embed policy origin is invalid: {value!r}") from exc
    try:
        host = _canonical_host(parsed.hostname or "")
    except ValueError as exc:
        raise ValueError(f"tenant embed policy origin is invalid: {value!r}") from exc
    secure = parsed.scheme == "https"
    local_http = allow_loopback_http and parsed.scheme == "http" and host in _LOOPBACK_HOSTS
    if (
        not (secure or local_http)
        or (secure and port is not None)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"tenant embed policy origin must be exact HTTPS: {value!r}")
    default_port = parsed.scheme == "https" or (parsed.scheme == "http" and port in {None, 80})
    authority = host if default_port else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"


@dataclass(frozen=True, slots=True)
class TenantEmbedPolicyService:
    """Validate and replay exact host, tenant, frame and CORS decisions."""

    policies: tuple[TenantEmbedPolicy, ...]
    allow_local_wildcard: bool = False

    def __post_init__(self) -> None:
        if not self.policies:
            raise ValueError("at least one tenant embed policy is required")
        seen_hosts: set[str] = set()
        normalized: list[TenantEmbedPolicy] = []
        for policy in self.policies:
            if not _IDENTIFIER.fullmatch(policy.policy_id):
                raise ValueError(f"tenant embed policy id is invalid: {policy.policy_id!r}")
            if policy.tenant == "*":
                if not self.allow_local_wildcard:
                    raise ValueError("wildcard tenant policy is allowed only in the local profile")
            elif not _IDENTIFIER.fullmatch(policy.tenant):
                raise ValueError(f"tenant embed policy tenant is invalid: {policy.tenant!r}")
            hosts = tuple(sorted({_canonical_host(host) for host in policy.hosts}))
            if not hosts:
                raise ValueError(
                    f"tenant embed policy {policy.policy_id!r} needs at least one host"
                )
            duplicates = seen_hosts.intersection(hosts)
            if duplicates:
                raise ValueError(
                    "tenant embed policy hosts must resolve exactly once: "
                    f"{', '.join(sorted(duplicates))}"
                )
            seen_hosts.update(hosts)
            frame_ancestors = tuple(
                dict.fromkeys(
                    ancestor
                    if ancestor == "'self'"
                    else _exact_origin(
                        ancestor,
                        allow_loopback_http=self.allow_local_wildcard,
                    )
                    for ancestor in policy.frame_ancestors
                )
            )
            if not frame_ancestors:
                raise ValueError(f"tenant embed policy {policy.policy_id!r} needs frame ancestors")
            cors_origins = tuple(
                dict.fromkeys(
                    _exact_origin(
                        origin,
                        allow_loopback_http=self.allow_local_wildcard,
                    )
                    for origin in policy.cors_origins
                )
            )
            normalized.append(
                TenantEmbedPolicy(
                    policy_id=policy.policy_id,
                    tenant=policy.tenant,
                    hosts=hosts,
                    frame_ancestors=frame_ancestors,
                    cors_origins=cors_origins,
                )
            )
        object.__setattr__(
            self, "policies", tuple(sorted(normalized, key=lambda item: item.policy_id))
        )

    def assess(
        self,
        *,
        request_host: str,
        tenant: str,
        request_origin: str = "",
    ) -> TenantEmbedAssessment:
        """Return the same evidence-linked decision for the same request context."""
        try:
            host = _canonical_host(request_host)
        except ValueError:
            host = request_host.strip().lower()
        origin = request_origin.strip()
        matches = tuple(policy for policy in self.policies if host in policy.hosts)
        findings: list[EmbedPolicyFinding] = []
        policy = matches[0] if len(matches) == 1 else None

        if not matches:
            findings.append(
                EmbedPolicyFinding(
                    finding_id=f"unknown-host:{host or 'empty'}",
                    kind=EmbedPolicyFindingKind.UNKNOWN_HOST,
                    severity=EmbedPolicySeverity.HIGH,
                    summary="Request host has no reviewed tenant policy",
                    detail=f"Host {host!r} is not present in the tenant policy registry.",
                    evidence_id="tenant-policy-registry",
                )
            )
        elif len(matches) > 1:
            findings.append(
                EmbedPolicyFinding(
                    finding_id=f"ambiguous-host:{host}",
                    kind=EmbedPolicyFindingKind.AMBIGUOUS_HOST,
                    severity=EmbedPolicySeverity.HIGH,
                    summary="Request host resolves to multiple tenant policies",
                    detail=f"Host {host!r} must resolve to exactly one reviewed policy.",
                    evidence_id="tenant-policy-registry",
                )
            )

        if policy is not None and policy.tenant not in {tenant, "*"}:
            findings.append(
                EmbedPolicyFinding(
                    finding_id=f"tenant-host-mismatch:{policy.policy_id}",
                    kind=EmbedPolicyFindingKind.TENANT_HOST_MISMATCH,
                    severity=EmbedPolicySeverity.HIGH,
                    summary="Verified tenant does not match the request host",
                    detail=(
                        f"Policy {policy.policy_id!r} binds host {host!r} to tenant "
                        f"{policy.tenant!r}, not {tenant!r}."
                    ),
                    evidence_id=policy.policy_id,
                )
            )

        cors_allowed = False
        if policy is not None and origin:
            try:
                canonical_origin = _exact_origin(
                    origin,
                    allow_loopback_http=self.allow_local_wildcard,
                )
            except ValueError:
                canonical_origin = origin
            # SAME-ORIGIN IS NOT CROSS-ORIGIN, and the CORS allowlist governs only the latter.
            #
            # Browsers send `Origin` on plenty of same-origin requests -- a `crossorigin` script
            # fetch, any POST, any `fetch(mode: "cors")` -- so treating "an Origin header is
            # present" as "this is a cross-origin caller" denies a page its own assets. That is
            # exactly what happened to the embedded console: some of its own Next.js chunks came
            # back 403 with a JSON body, the browser refused to execute a script served as
            # application/json, React never finished hydrating, and the console sat on
            # "Connecting..." while every other request on the same origin succeeded. An empty
            # `cors_origins` (the correct posture for a tenant that federates with nobody) made it
            # certain rather than intermittent.
            same_origin = canonical_origin == f"https://{host}" if host else False
            cors_allowed = same_origin or canonical_origin in policy.cors_origins
            if not cors_allowed:
                findings.append(
                    EmbedPolicyFinding(
                        finding_id=f"origin-denied:{policy.policy_id}",
                        kind=EmbedPolicyFindingKind.ORIGIN_DENIED,
                        severity=EmbedPolicySeverity.HIGH,
                        summary="Cross-origin caller is not allowed for this tenant",
                        detail=(
                            f"Origin {origin!r} is not in policy {policy.policy_id!r}'s exact "
                            "CORS allowlist."
                        ),
                        evidence_id=policy.policy_id,
                    )
                )

        findings.sort(key=lambda item: (item.severity.value, item.finding_id))
        actions = tuple(
            dict.fromkeys(
                {
                    EmbedPolicyFindingKind.UNKNOWN_HOST: (
                        "Add the exact routed host to one reviewed tenant policy before retrying."
                    ),
                    EmbedPolicyFindingKind.AMBIGUOUS_HOST: (
                        "Remove duplicate host bindings so exactly one tenant policy resolves."
                    ),
                    EmbedPolicyFindingKind.TENANT_HOST_MISMATCH: (
                        "Investigate the identity-to-host mismatch; do not broaden the policy."
                    ),
                    EmbedPolicyFindingKind.ORIGIN_DENIED: (
                        "Review the caller origin and add it only through an approved "
                        "policy change."
                    ),
                }[finding.kind]
                for finding in findings
            )
        )
        return TenantEmbedAssessment(
            request_host=host,
            request_origin=origin,
            tenant=tenant,
            policy_id=policy.policy_id if policy is not None else "",
            frame_ancestors=policy.frame_ancestors if policy and not findings else ("'none'",),
            cors_allowed=bool(origin) and cors_allowed and not findings,
            findings=tuple(findings),
            suggested_actions=actions,
        )
