# ARCHITECTURE: `journey-portal`

Hexagonal (ports-and-adapters), exactly like the rest of the catalog: a pure-stdlib domain core,
typed ports, swappable adapter families selected by one profile env var, and a green offline gate.
The runtime profiles are `local`, `gcp`, `platform` and the fail-fast `onprem` seam.

## The hexagon

```
                       ┌─────────────────────────────────────────────┐
   RM shell (React) ─▶ │  api/  FastAPI BFF                           │
   Ops shell (Angular)▶│    proxy routes  +  journeys/personas/whoami │
                       │                                              │
                       │  domain/ (pure stdlib, no framework/cloud)   │
                       │    catalog.py           journey model+routing│
                       │    identity_injection.py  the trust boundary │
                       │    audit.py               hash-chain verifier │
                       │                                              │
                       │  ports/  adapters/{local,gcp,platform,onprem}│
                       │    IdentityPort ───────  identity            │
                       │    UpstreamClientPort ─  upstream (proxy)    │
                       │    AccessAuditPort ─────  access evidence     │
                       │    BffSigningKeyPort ──  `cdd-sow-research` Mode 5 identity │
                       │    SubjectTokenPort ───  end-user grant token │
                       └─────────────────────────────────────────────┘
                                    │                    │
                        seeded persona / IAP        httpx -> embedded app
```

- **`domain/`** is pure stdlib (the parity test forbids framework/cloud imports here). It holds the
  journey `catalog` (validate config, resolve apps, build reverse-proxy target URLs) and the
  `identity_injection` policy (the header-rewrite plan that is the security core). Both are
  deterministic and fully unit-tested.
- **`ports/`** names `UpstreamClientPort` for the reverse-proxy edge, `AccessAuditPort` for
  content-free access evidence, `BffSigningKeyPort` for the portal's own service identity (the
  `private_key_jwt` credential `cdd-sow-research`'s Mode 5 broker verifies) and `SubjectTokenPort` for the
  end-user token that grant exchanges. Identity uses the shared commons port
  `hex_service_kit.identity.IdentityPort`, so it is not re-declared here. `ports/__init__.py`
  `__all__` and the parity contract test together are the source of truth for what exists.
- **`adapters/`** has one implementation per port per profile. Identity: seeded personas (local),
  IAP assertion verification (gcp/platform, lazy `google-auth`), fail-fast placeholder (onprem).
  Upstream: local httpx, authenticated HTTPS delegates (gcp/platform), fail-fast placeholder
  (onprem). Access audit: a local SQLite hash chain, structured managed-profile logs captured by
  the regional evidence sink, and a fail-fast on-prem placeholder. BFF signing: a generated key
  in a gitignored file (local), a non-exportable Cloud KMS key version (gcp/platform, lazy
  `google-cloud-kms`), fail-fast placeholder (onprem). Subject token: an obviously fictional
  offline placeholder (local), a refusal naming the outstanding deployment inputs (gcp/platform),
  fail-fast placeholder (onprem).
- **`config.py`** is the DI: `Settings` from env, a `Container` with one `cached_property` per port
  bound by the active profile, the validated `JourneyCatalog`, and the reviewed tenant embed
  policies.
- **`api/`** is the only place FastAPI appears. It wires the container into routes, resolves the
  verified principal, evaluates the request host/tenant/origin before route execution, emits exact
  CSP/CORS headers, and keeps the commons `resolve_bind_host` loopback default.

## Request pipelines

### Proxied API call (identity injected)

```
browser ─▶ shell proxy ─▶ BFF  /apps/cdd-sow-research/api/v1/cdd
  get_principal: resolve the portal principal (persona cookie in local; IAP assertion in secure);
                 the browser's X-Dev-Persona / Authorization are dropped before resolving
  TenantEmbedPolicyService: bind request host to exactly one reviewed tenant policy;
                 deny an identity/host or Origin mismatch before the app route runs
  record embed-policy:allowed|denied in the content-free audit chain
  build_injection_plan(principal, profile, inbound):
                 strip {x-dev-persona, x-goog-iap-jwt-assertion, authorization};
                 set the portal-verified identity (persona id in local; edge IAP in secure)
  sanitize_request_headers: drop hop-by-hop + stripped identity, then inject
  api_target(mount, "v1/cdd") -> http://cdd-sow-research-backend/v1/cdd   (the /apps/cdd-sow-research/api prefix stripped)
  UpstreamClientPort.forward(...) -> UpstreamResponse
  rebuild response (framing headers recomputed; set-cookie preserved)
```

### Proxied UI asset (no identity)

```
browser ─▶ shell proxy ─▶ BFF  /apps/cdd-sow-research/_next/static/x.js
  ui_target(mount, full_path) -> http://cdd-sow-research-ui/apps/cdd-sow-research/_next/static/x.js   (full path unchanged)
  the app is basePath-aware (built with NEXT_PUBLIC_BASE_PATH=/apps/cdd-sow-research), so its own URLs resolve
```

## Why two proxy hops, all same-origin

The browser only ever talks to the shell's origin. The shell (Next.js `rewrites()` or Angular
`proxy.conf.json`) forwards `/apps/*` and `/v1/*` to the BFF; the BFF forwards `/apps/<id>/*` to
each embedded app. Because it is one origin end to end, every embedded iframe is first-party: no
CORS, no third-party cookies, and the portal can own the framing policy. The host selects exactly
one tenant policy; the verified principal must match it; the policy's exact CSP `frame-ancestors`
and CORS origin set drive the response.
In production the shells and BFF are separate private Cloud Run services behind one HTTPS load
balancer and IAP. Host rules select the RM or Ops shell; path rules send `/v1`, `/apps` and
`/agent` to the BFF. Embedded UIs/APIs are internal-only services invoked by the BFF with
workload-identity ID tokens.

## Identity model

The portal is itself an `embeddable-secure-ui` consumer. Its own identity is server-verified
(`IdentityPort`), and its distinctive job is to then re-present that identity to each embedded app
the way that app expects, without ever trusting the browser:

- **local**: resolve a seeded persona from the portal cookie; inject `X-Dev-Persona=<persona id>`.
- **secure**: verify the IAP assertion the edge injected; forward that exact assertion; each app
  re-verifies it (defense in depth). Per-hop OAuth2 token exchange (OBO) to the apps is the
  documented next hardening layer, not built in this slice.
- **tenant boundary**: resolve the external host to one reviewed policy, require the verified
  tenant to match, and reject unapproved cross-origin requests before proxy side effects.

See `SPEC.md` for the invariant and `docs/embedding-and-identity.md` for the deployment shapes.

## What is deliberately not here

- No `journey-portal` cross-origin loader / postMessage bus / Web Component host. `cdd-sow-research` proves modes 4/5
  independently; `journey-portal` remains the canonical same-origin mode-1 host.
- No shared session store or tenant-specific IAP issuer registry. Host-bound tenant framing/CORS
  policy is implemented; the named IAP deployment still uses one reviewed audience and issuer
  contract.
- No business-data persistence. The local profile persists only pseudonymized, content-free
  portal-access metadata in an append-only SQLite hash chain selected by
  `PORTAL_LOCAL_AUDIT_DB`. Request bodies, queries, credentials and identity assertions are
  excluded. The keyed actor and tenant references remain pseudonymous personal data and receive
  the same access and retention controls. The standalone `gcp` profile writes the bounded event
  to Cloud Logging; the production `platform` profile maps it to the `agent-observability` wire contract and posts
  it with an audience-bound workload identity token. Both fail closed if delivery is not
  acknowledged.
- No per-hop OBO token exchange. It remains an explicit deferred item.
