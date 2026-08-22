# Embedding and identity: how the portal composes and secures the apps

This is the portal's side of the story the per-app `docs/embedding-and-identity.md` guides tell.
Those guides describe how one app embeds into a cooperative host; this describes the host.

## The deployment shapes

| Shape | Status | The browser sees | Identity |
|---|---|---|---|
| Local dev | Implemented | The shell origin (`:3000` RM, `:4200` Ops). The shell dev server reverse-proxies the BFF; the BFF reverse-proxies each app. All same-origin. | Seeded personas via the portal cookie; the BFF injects `X-Dev-Persona` per app. No IdP. |
| Cloud, same-origin behind IAP | Implemented (identity path) | One portal origin behind an HTTPS load balancer + Identity-Aware Proxy. A static shell build and the BFF and each app are services behind that one edge. | IAP verifies the corporate IdP (via Workforce Identity Federation) once; the BFF forwards the edge assertion; each app re-verifies it. One sign-on across every app. |
| On-prem | Placeholder | The client's own edge | The client binds its own IdP adapter and egress path (`docs/onprem-migration.md`). |

The cross-origin loader / postMessage shapes (modes 4/5 in the reference guide) are out of scope:
they are not Hrz9 host modes. Doc1 demonstrates them independently. The portal is the cooperative
same-origin host those guides assume,
which is why it needs no loader and no third-party-cookie handling.

## Why same-origin end to end

The browser only ever loads the shell's origin. The shell forwards `/apps/*` and `/v1/*` to the
BFF; the BFF forwards `/apps/<id>/*` to each app. Because it is one origin the whole way:

- every embedded `<iframe src="/apps/<id>/">` is first-party: no CORS, no third-party cookies;
- the portal owns the framing policy (CSP `frame-ancestors 'self'`), and each app's own
  `frame-ancestors 'self'` is satisfied because, from the browser's view, the framed document is
  same-origin with the shell;
- the portal cookie that selects the persona is first-party and travels on every API call
  automatically.

Each embedded app is built with `NEXT_PUBLIC_BASE_PATH=/apps/<id>` (so it emits and serves its own
`/apps/<id>/...` URLs) and `NEXT_PUBLIC_EMBED=1` (so its own header/nav chrome is hidden and the
portal owns the chrome).

## The proxy routing

- `/apps/<id>/api/<path>` -> `<app api_upstream>/<path>`. The `/apps/<id>/api` prefix is stripped
  because the app backend serves `/v1/...` at its own root. Identity is injected here.
- `/apps/<id>/<path>` -> `<app ui_upstream>/apps/<id>/<path>`. The full path is forwarded unchanged
  because the app UI is basePath-aware. No identity injection (static assets).

## The identity trust boundary (the security core)

The portal is itself a server-verified `IdentityPort` consumer. Its distinctive job is to
re-present that verified identity to each embedded app without ever trusting the browser. On every
proxied API request:

1. **Resolve the portal principal.** Local: the persona cookie selects a seeded persona; the
   browser's `X-Dev-Persona` and `Authorization` are dropped before resolving, so a browser cannot
   pick a persona by setting a header. Secure: verify the IAP assertion the edge injected.
2. **Strip the client-spoofable identity headers** from the inbound request:
   `X-Dev-Persona`, `x-goog-iap-jwt-assertion`, `Authorization`.
3. **Inject the portal-verified identity:** local injects `X-Dev-Persona=<resolved persona id>`;
   secure forwards the edge-signed `x-goog-iap-jwt-assertion` (the value the portal itself
   verified). Strip precedes inject, so the injected identity always wins.
4. **Each app re-verifies.** In secure mode every embedded app verifies the IAP assertion again
   with its own `IapIdentityAdapter`: distinct trust boundaries, defense in depth.

The invariant, stated once: **a browser-asserted identity never reaches an embedded app.** It is
implemented in `src/journey_portal/domain/identity_injection.py`, unit-tested, and scored as
`identity_isolation` (threshold 0.99) in the eval gate. Run `make demo` to see it worked through on
real cases.

Why personas are shared: every embedded app seeds the shared-commons `DEFAULT_PERSONAS`
(`analyst`/`approver`/`auditor`/`other-tenant`), so the persona id the portal injects always
resolves inside each app. A journey is a set of apps; a persona is a role within a tenant. They are
orthogonal, which is why one persona drives every app in a journey.

## Tenant host, framing and CORS boundary

`PORTAL_TENANT_EMBED_POLICIES_JSON` is a reviewed, non-secret registry keyed by stable policy id.
Each policy binds one tenant to exact routed hosts, CSP frame ancestors and exceptional CORS
origins. The request path is deterministic:

1. verify the principal through the profile's `IdentityPort`;
2. resolve the request host to exactly one policy;
3. require the verified tenant to equal that policy tenant;
4. reject an unapproved `Origin` before any proxied route can cause a side effect;
5. emit that policy's `frame-ancestors` and exact CORS response; and
6. append one content-free `embed-policy:allowed` or `embed-policy:denied` event.

The RM and Ops production static servers consume the same registry and select their framing policy
by exact Host. An unknown or malformed shell host gets `frame-ancestors 'none'`. Terraform requires
every routed RM/Ops hostname to resolve exactly once and sends one canonical JSON policy document
to the BFF and both shells. `/v1/embed-policy` exposes the applied decision, evidence id, findings
and suggested action for reviewers.

Local remains offline and uses one explicit loopback-only demo policy that permits both seeded
tenants. Wildcard tenant policy is rejected in every managed profile.

### The directive is never blank

An empty CSP directive (`frame-ancestors ` with nothing after it) is a parse error that browsers
discard, and the `'self'` comparison that adds `X-Frame-Options` fails against it too, so a blank
value would remove the clickjacking control from both channels at once with nothing in the
response to show it. Every surface here resolves the value in **three** states instead of two:

| Surface | Unset | Set, naming no origin | Set to origins |
| --- | --- | --- | --- |
| BFF (`api/tenant_security.py`) | registry required outside `local` | policy refused when the registry is built | that policy's ancestors, or `'none'` on any finding |
| RM / Ops static shells (`static_server.py`) | `'self'` | `ValueError`, the shell refuses | that value, after exact-origin validation |
| RM dev shell (`ui-rm/next.config.mjs`) | `'self'` | refuses at config load | that value, normalised |
| Deployment inputs (`DEPLOY_FRAME_ANCESTORS_JSON`) | required | `DeploymentConfigError` | that list, after exact-origin validation |

A total lockdown stays expressible everywhere as `'none'`, so refusing an empty value costs an
operator nothing.

## The next hardening layer (documented, not built in this slice)

Per-hop OAuth2 token exchange (OBO) so the BFF presents a purpose-scoped token to each app rather
than forwarding the edge assertion; tenant-specific IAP issuer/audience variants; and the
cross-origin loader for non-cooperative hosts. These are the same roadmap items the reference
`cdd-sow-research` guide tracks.
