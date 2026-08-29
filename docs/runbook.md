# Runbook: Hrz9 Journey Portal

## Ownership and prerequisites

Name a deployment owner, security owner, DNS/IAP owner, operations owner and evidence approver.
Before planning, confirm the project, remote-state backend, billing/quota, approved region,
Artifact Registry, image digests, IAP client, DNS zone, UI/API-specific secret names,
notification channels, reviewed GCS state bucket/prefix, and apply window. Terraform computes the
exact IAP audience during the first apply; operators must not guess it.

Required operator permissions should be time-bounded and separated: Terraform state, Cloud Run,
load balancing/IAP, DNS/certificates, service accounts, Secret Manager IAM, Logging/Monitoring,
and optionally Org Policy. Runtime identities receive only Cloud Run invocation and named secret
access. Do not create service-account keys.

## Build and release images

The `release immutable images` workflow uses workload identity and pushes the BFF/RM/Ops builds
under run-specific quarantine tags with max provenance and SBOMs. It scans the exact build digest,
blocks high/critical vulnerability findings, signs that digest, and only then promotes it to the
reviewed version tag. Digest metadata artifacts are retained for 90 days. This repo does not
control Artifact Registry cleanup; the operator must verify that every current and rollback digest
for all 15 services remains pullable through the rollback window. Embedded application teams
provide equivalent reviewed UI/API digests. No deployment consumes a mutable tag.

## Offline gates

```bash
make check
make demo-selftest
make portability
cd ui-rm && npm ci && npm run lint && PORTAL_STATIC_EXPORT=1 npm run build
cd ../ui-ops && npm ci && npm run lint && npm run build
make tf-validate
```

Both shell lockfiles override the reviewed patched `postcss` release, and the RM shell also
overrides `sharp` to 0.35.3 or newer within the lockfile. The Ops shell additionally overrides
`@modelcontextprotocol/sdk` to 1.30.0, which is the first release that accepts the patched
`@hono/node-server` 2.x line, and `sockjs`'s `uuid` to 11.1.1 or newer; neither transitive
advisory has a fixed parent release, so the override is the only non-suppressing fix. The npm
audit gate permits no high or critical findings. Neither shell runtime image contains Node or
build dependencies.

The Ops shell is Angular 22, which refuses to run on anything below Node `^22.22.3 || ^24.15.0
|| >=26` and exits with status 3. Its CI job and its build image are on Node 24; the RM shell
stays on Node 20.

`scripts/portability_demo.py` proves only the channel, identity and runtime seams it executes.
It explicitly excludes model/data portability, working on-prem, OBO and multi-tenancy.

For the local profile, `PORTAL_LOCAL_AUDIT_DB` selects the append-only SQLite portal-access
ledger and defaults to `.local/portal-access-audit.sqlite3`. The adapter creates owner-only key
and checkpoint files next to it unless `PORTAL_LOCAL_AUDIT_KEY_FILE` and
`PORTAL_LOCAL_AUDIT_CHECKPOINT` select separately protected locations. The HMAC-protected
checkpoint binds the retained record count and head hash, so tail deletion fails verification.
Writes coordinate SQLite and the checkpoint with an owner-only cross-process lock. A signed
pending state lets startup recover either side of an interrupted database/checkpoint commit
without accepting any other state. Retain a copy of the checkpoint in the approved evidence
location at review milestones. A missing database or checkpoint beside an existing key, or any
failed integrity result, is an incident: stop relying on the ledger, preserve the files and
restore only from a reviewed backup. Treat actor and tenant references as pseudonymous personal
data.

Managed profiles require `PORTAL_AUDIT_HMAC_KEY` from an exact Secret Manager version. The value
must contain at least 32 random bytes. Rotate it only through a reviewed release that retains the
prior key identifier with its evidence window. Cloud Logging delivery is synchronous and a failed
write returns 503 before the request reaches an embedded application.

Managed profiles also require `PORTAL_TENANT_EMBED_POLICIES_JSON`. Terraform produces it from
`tenant_embed_policies` and passes the same canonical document to the BFF, RM shell and Ops shell.
Each policy binds one stable tenant id to exact routed hosts, frame ancestors and CORS origins.
The BFF rejects unknown hosts, tenant/host mismatches and unapproved origins before route
execution, then writes the allow/deny assessment to the audit sink. The static shells fail framing
closed with `frame-ancestors 'none'` if their Host does not resolve exactly once. Use
`GET /v1/embed-policy` through each approved hostname to retain the applied policy evidence.

## Plan and apply

1. Copy `.env.example` to `.env` and `.env.secrets.example` to `.env.secrets`. Put only the IAP
   OAuth client secret in `.env.secrets`; the portal audit HMAC value stays in Secret Manager and
   `.env` names only its resource and exact numeric version. All other non-secret deployment
   inputs belong in `.env`.
   Run `chmod 600 .env.secrets`; the loader rejects group-readable or world-readable secret files.
2. Run `python scripts/deployment_config.py check`. The loader rejects placeholders, mutable
   images, wildcard origins, an invalid tenant id, missing owners, missing notification channels
   and mixed secret placement. Render non-secret inputs with
   `python scripts/deployment_config.py render`.
3. Start from a reviewed example and replace every fictional value. Keep
   `iap_jwt_audience = ""` and `iap_members = []` for bootstrap.
4. Run Terraform through the loader so the IAP secret exists only in the child process:
   `python scripts/deployment_config.py terraform -- init`, followed by
   `python scripts/deployment_config.py terraform -- plan -out=reviewed.tfplan`. The runner
   rejects local state, competing inputs and ambient Terraform overrides, then verifies the exact
   GCS backend metadata.
5. Scan the saved plan JSON for mutable tags, public invokers, regions outside the
   allowlist, wildcard origins, unexpected deletes, and secret values.
6. For an existing BFF, follow `infra/terraform/MIGRATION.md` and reject replacement.
7. Apply stage one with Org Policies and retention lock disabled. It creates
   the perimeter in explicit dry-run mode and creates no user access
   grants.
8. Apply only a saved reviewed plan through
   `python scripts/deployment_config.py terraform -- apply reviewed.tfplan`. Record
   `python scripts/deployment_config.py terraform -- output -raw computed_portal_iap_audience`,
   copy it exactly into `iap_jwt_audience`, add approved `iap_members`, then save and review a
   second plan. A mismatch fails before any backend-scoped grant is created.
9. Apply stage two. Confirm DNS resolves, certificates are active, IAP denies an unauthenticated
   request, and both approved RM and Ops users can sign in.
10. Confirm the BFF reaches every `INGRESS_TRAFFIC_INTERNAL_ONLY` embedded UI/API through its
   dedicated Direct VPC `ALL_TRAFFIC` egress and Private Google Access subnet.
11. Send a valid IAP-authenticated request through the load balancer, confirm assertion
   verification succeeds in the portal and each embedded app against the same computed edge
   audience, and retain the matching Cloud NAT log entry for IAP public-key retrieval. Treat
   key-fetch timeout or verifier failure as a deployment blocker.
12. Run every configured journey through the shell, BFF, embedded UI and API. Confirm Doc1 at
   `/agent`, `/agent/api` and the `/apps/cdd-sow-research` redirect.
13. Record both plans/applies, computed audience, state serial, service revisions, image digests,
   backend-scoped IAP policy, VPC connectivity evidence, DNS and certificate status, browser
   evidence and approver sign-off.
14. Review VPC-SC dry-run denials. Enforcement remains code-disabled while unrestricted Cloud NAT
   exists; replace it with approved restricted egress before adding an enforcement slice. Enable
   Org Policies in a separate approved plan. Lock the 180-day default retention only after
   restore testing and legal review.

No source-controlled fixture is live apply evidence.

## Health and alerting

- `/healthz` checks BFF profile and region. RM probes `/`; Ops probes `/healthz`.
- Embedded UI probes its configured TCP port; embedded APIs probe `/healthz`.
- Load-balancer request logging is sampled at 100 percent and copied into the regional audit
  bucket. The 403 policy should route to institution notification channels.
- Cloud NAT logs every translation and error from the dedicated BFF subnet. Monitor denied or
  failed IAP signing-key retrieval and review any unexpected public destination.
- Terraform alerts on IAP denials, service-account key creation or upload, VPC-SC denials and CMEK
  changes. Add institution thresholds for sustained 5xx, latency, instance saturation,
  certificate expiry, Secret Manager denial and unexpected revision creation.
- The production `platform` profile sends every content-free portal event to the exact
  `PORTAL_OBSERVABILITY_URL` using a token minted for
  `PORTAL_OBSERVABILITY_AUDIENCE`. Alert on Hrz5 delivery failures; the portal fails closed
  before forwarding when Hrz5 or workload-token acquisition is unavailable.

## Rollout and rollback

Use one digest change per reviewed rollout. Keep minimum instances and concurrency within tested
limits. After apply, compare the new revision's health, error rate and latency before shifting all
traffic. Roll back by restoring the prior digest variables and applying the saved reviewed plan.
Do not roll state backwards over newer infrastructure. For a compromised image or identity
registration, revoke IAP access or disable the forwarding rule first, then restore a known-good
digest and re-run the identity isolation checks.

## Incidents

For identity spoofing, remove edge access, retain IAP/LB/Cloud Run evidence, and verify that no
browser Authorization, persona or IAP header crossed the BFF. For upstream compromise, revoke the
BFF invoker grant and secret access for that app. For certificate or DNS failure, preserve the
address and restore the prior record. For region-policy denial, do not bypass the allowlist:
escalate to the residency owner.

## Rotation and recovery

Rotate IAP clients, app secrets and service revisions in separate changes. UI and API secrets stay
in separate environment maps and receive grants only to their distinct runtime identities. Secret
values stay in Secret Manager and are referenced by name. Test state recovery and Logging-bucket
access before locking retention. Rehearse remote-state restore, DNS rollback, prior-image
redeploy, IAP member revocation and notification escalation at least once per release.

## The Doc1 Mode 5 BFF signing key

The portal authenticates to Doc1's grant endpoint with `private_key_jwt`. Under every managed
profile the signer is a Cloud KMS key version: the private key is non-exportable, and the only
thing recorded anywhere is `PORTAL_BFF_SIGNING_KEY_VERSION` plus the `kid`
(`PORTAL_BFF_SIGNING_KID`) that the published JWK set and every assertion header carry.

Rotation, without a token outage:

1. create the new KMS key version and note its `kid`;
2. add the OUTGOING public JWK to `PORTAL_BFF_ACCEPTED_PUBLIC_JWKS` and deploy, so
   `/.well-known/cdd-sow-research-bff-jwks.json` publishes both keys;
3. wait at least the JWKS cache lifetime (`public, max-age=300`) so every relying party has
   refetched;
4. switch `PORTAL_BFF_SIGNING_KEY_VERSION` and `PORTAL_BFF_SIGNING_KID` to the new version and
   deploy. New assertions carry the new `kid`; assertions minted in flight still verify;
5. after the overlap window recorded in the dossier, empty `PORTAL_BFF_ACCEPTED_PUBLIC_JWKS`,
   deploy, and disable the old key version.

Emergency revocation reverses the order: disable the KMS version first (which stops signing
immediately), then republish the JWK set without it. Capture the published JWK set before and
after, and record both in the evidence pack.

`PORTAL_SESSION_SIGNING_KEY` is a separate Secret Manager value and rotates independently. It
keys the CSRF tokens and the hashed session binding, so rotating it invalidates every outstanding
CSRF token: outstanding tokens live 90 seconds, so rotate during a quiet window and expect a
small number of retried grant attempts.

Owners, dates and the still-outstanding inputs are recorded in
[`named-deployment-dossier.md`](named-deployment-dossier.md).

## Deferred items and blockers

The manual `live profile integration` workflow uses keyless WIF to obtain an IAP audience-bound ID
token and checks both HTTPS shell roots, managed health and region, verified identity, and both
journey feeds. It also reaches all seven embedded application health routes through the correct RM
or Ops origin, loads every iframe route and its base-path-scoped build assets, and rejects local
profiles. Configure the `live-integration` GitHub environment variables for project, Singapore
region, URLs, IAP client, resource name prefix, and the exact 15-component image-digest manifest,
plus its two WIF secrets, only after a named deployment exists. The workflow first verifies those
exact GCP resources, their IAP settings, region and exact reviewed images; it then uses a separate
audience-bound token for redirect-disabled positive and unauthenticated-negative HTTP checks.
Grant the
`GCP_LIVE_INTEGRATION_SERVICE_ACCOUNT` backend-scoped IAP access by including its
`serviceAccount:` member in `DEPLOY_IAP_MEMBERS_JSON`; do not grant project-wide IAP access.

Live completion requires institution-provided project/IAM, images, IAP/WIF, DNS/TLS, secrets,
users, notification channels and apply approval. It also requires the Hrz5 URL and audience plus
a backend-scoped invoker/caller grant for the portal service account. Per-hop OBO and
tenant-specific issuer/audience variants remain deferred.
