# SPEC: Hrz9 Journey Portal Shell

Locked decisions, pinned stack, and contracts. The deepest authority on intent; where this and
prose elsewhere disagree, this wins.

## Purpose

Compose the built P1 app UIs into one UI per persona (an RM Journey and an Ops Journey) via
same-origin reverse-proxy embedding, with the portal-verified identity injected into every embedded
app. It is the catalog's first runnable proof of the embeddable-micro-frontend claim.

## Locked decisions

1. **Single repo, one BFF, two shells.** A shared FastAPI BFF owns the proxy, the journey config,
   and identity injection. Two thin UI shells consume it: `ui-rm` in React/Next.js, `ui-ops` in
   Angular. The two-framework split is a requirement (proof of host-framework agnosticism), not an
   accident; the shells share no UI code and both compose the identical portal.
2. **Mode-1 same-origin embedding only.** The portal is the cooperative reverse-proxy host the
   per-app embedding guides assume. The cross-origin loader / postMessage SDK (modes 4/5) stays out
   of scope for Hrz9. Doc1 demonstrates those portable modes independently. Content-driven iframe
   height therefore uses a sized
   container, not a resize protocol.
3. **Journeys are config.** `config/journeys.yaml` maps journeys to apps and apps to upstreams,
   with `${ENV:-default}` interpolation. Adding or recomposing a journey is a config edit.
4. **Identity is server-verified and injected; never client-asserted.** On every proxied API call
   the portal strips `X-Dev-Persona`, the IAP assertion, and `Authorization` from the inbound
   request, then injects the identity it resolved itself. Local resolves a seeded persona from the
   portal cookie; secure verifies and forwards the IAP assertion (each app re-verifies).
5. **No third-party / Google-account login mode.** Local is offline personas; cloud is IAP + WIF.
   This keeps the SDK-free gate green.
6. **Same shared-commons personas as the embedded apps.** The portal reuses `DEFAULT_PERSONAS`
   (`analyst`/`approver`/`auditor`/`other-tenant`) so an injected persona id always resolves inside
   each embedded app. A journey is a set of apps; a persona is a role. They are orthogonal.
7. **Host-bound tenant embedding policy.** Every non-health request host resolves to exactly one
   reviewed tenant, framing and CORS policy. The verified principal tenant must match it, and an
   unapproved Origin is rejected before route side effects. The BFF and both production shells
   consume one canonical policy document. Managed profiles reject wildcard tenants.
8. **Region selected at deploy time and validated** against the residency allowlist, defaulting to `asia-southeast1` for any managed deployment, like the rest of the catalog.

## Pinned stack

- Python `>=3.12`; FastAPI + uvicorn; `httpx` for the reverse proxy; `pyyaml` for config.
- Catalog commons, pinned by tag: `hex-service-kit[fastapi]` (identity, netdefaults, web glue),
  `agent-eval-kit` (the eval scaffold), `pii-kit`. No copy-paste of these.
- Managed cloud SDK (`google-auth`) is in the `[gcp]` extra only, lazy-imported; the local/onprem
  gate installs none of it.
- Ruff pinned exactly (`0.15.18`); mypy strict on `src`.
- Shells: Next.js 16 (React 19) for `ui-rm`; Angular 19 for `ui-ops`.

## Contracts

### BFF endpoints (consumed by the shells)

- `GET /healthz` -> `{status, profile, region}`.
- `GET /v1/journeys` -> the journey catalog: each journey with its ordered apps and their
  same-origin `ui_base` / `api_base` / `mount_path`.
- `GET /v1/personas` -> the seeded persona list (empty outside local).
- `GET /v1/whoami` -> the portal-verified principal (subject, tenant, principals, source, persona).
- `GET /v1/embed-policy` -> the applied host/tenant/framing/CORS decision, findings, evidence id
  and suggested actions.
- `POST /v1/session/persona {id}` -> select the demo persona (local only; sets a cookie); empty id
  clears to the default.
- `ANY /apps/{id}/api/{path}` -> the app's backend, with identity injected and the `/apps/<id>/api`
  prefix stripped.
- `ANY /apps/{id}/{path}` -> the app's basePath-aware UI, full path forwarded unchanged.
- `GET /.well-known/doc1-bff-jwks.json` -> the portal's published public signing keys (RFC 7517).
  Unauthenticated and cacheable by design: it is public key material a relying party fetches
  before it holds any credential of ours. Only public JWK members are ever emitted.
- `GET /v1/doc1/embed/csrf` -> one short-lived CSRF token bound to this session and to the exact
  grant action. Private, no-store.
- `POST /v1/doc1/embed/grant {instance_id}` -> the Doc1 Mode 5 brokered grant. The client names
  the embed instance and nothing else; the client id, the scopes and the whole host authorization
  proof come from reviewed policy and the portal's verified session. Private, no-store.

### The Doc1 Mode 5 contract (the service-identity boundary)

The portal is the BFF half of Doc1's cross-origin embedded grant. What it owns:

- it holds the ONLY service identity in the exchange, and mints an RFC 7523 `private_key_jwt`
  client assertion whose issuer and subject are the BFF client id, whose audience is the exact
  grant endpoint, whose lifetime is at most 60 seconds and whose JTI is fresh per assertion,
  matching what Doc1's `PrivateKeyJwtVerifier` validates including its replay store;
- it publishes the matching public keys so Doc1 can pin the client;
- it refuses a grant request unless the exact `Origin`, the `Sec-Fetch-Site` value and a
  session-bound CSRF token all check out, and it does so BEFORE minting a credential or calling
  the broker, so a forged request never consumes a JTI on Doc1;
- the host authorization proof carries the PORTAL-VERIFIED principal's subject, a hashed session
  binding and a fresh opaque user-intent id. Nothing the browser asserts enters the proof.

A cross-repo fixture verifies a portal-minted assertion against Doc1's actual verifier, imported
from the sibling checkout rather than vendored, with the replay, tamper, expiry, wrong-audience
and unregistered-client negatives all refused.

### The identity-injection invariant (the security contract)

For any inbound request, the headers forwarded to an embedded app backend:

- contain the portal-resolved identity (`X-Dev-Persona=<resolved persona>` in local; the
  edge-signed `x-goog-iap-jwt-assertion` in secure), and
- contain NONE of the client-spoofable identity headers as supplied by the browser.

This is enforced in `domain/identity_injection.py`, unit-tested in `tests/test_identity_injection.py`,
and scored as the `identity_isolation` metric (threshold 0.99) in the eval gate.

### The tenant embedding invariant

For every non-health request, `domain/embed_policy.py` deterministically requires:

- the exact request host to resolve to one reviewed policy;
- the verified principal tenant to match that policy tenant; and
- any cross-origin caller to match that policy's exact CORS allowlist.

Any finding yields `frame-ancestors 'none'`, a 403 before route execution, and a content-free
`embed-policy:denied` audit event. Allowed responses emit only the resolved policy's frame/CORS
headers and record `embed-policy:allowed`.

### Profiles

`PORTAL_PROFILE`: `local` (SDK-free offline; seeded personas, httpx proxy), `gcp`
(standalone managed deployment with IAP and Cloud Logging), `platform` (the managed deployment
with the same IAP/private upstream transport plus synchronous Hrz5 `/v1/audit` delivery), and
`onprem` (fail-fast placeholders for identity, audit and egress). Every port binds in every
profile; local and onprem import with no cloud SDK.

The variable has no default, and an unset one is NOT a choice. `local` is the offline profile you
select deliberately, not what you fall into. With `PORTAL_PROFILE` unset the portal refuses to
serve: the only tenant embed registry an unconfigured run could have is the seeded one whose
tenant is the wildcard `*`, that wildcard is a relaxation granted to a chosen `local` alone, and
the domain requires at least one reviewed policy, so there is nothing left to serve with. The
refusal happens while settings load, before any credential is inspected and with no cloud SDK
involved, and each request answers 503 naming the variable. An unknown or mis-capitalised value
(`Local`, `GCP`) is refused the same way rather than selecting neither the relaxations nor the
restrictions. Every shipped path sets the variable explicitly: `.env.example`, the Makefile, the
Cloud Run service and CI.

The platform access adapter deterministically maps each content-free `PortalAccessEvent` to the
Hrz5 contract. Actor and tenant remain deployment-keyed pseudonyms; prompt, response and citation
fields are empty; workload identity supplies the Hrz5 audience-bound bearer. Delivery failure
fails the portal request closed.

## The gate (green before anything lands)

`ruff check src tests eval` + `ruff format --check src tests eval` + `mypy src` +
`pytest -m 'not integration'` + `python eval/run_eval.py` (exit 0). SDK-free. The shells are
typechecked separately (`npm run lint` in each).
