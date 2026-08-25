---
type: Deployment Input Dossier
title: Hrz9 named deployment
description: Non-secret decision and evidence record for the journey portal acting as the Doc1 Mode 5 BFF.
status: draft
---

# Hrz9 named deployment dossier

This dossier is the entry gate for one real Hrz9 installation, and specifically for the half of
Doc1's cross-origin embedded grant (Mode 5) that this repo owns. Doc1's own dossier names the
portal's origin as the BFF and pins its `private_key_jwt` client against a JWK set this portal
publishes, so until this file is complete Doc1's Mode 5 cannot be signed off.

Do not put credentials, private keys, subject tokens, session values or customer data here.
Record only approved resource names and evidence locations. Secret payloads belong in the
institution's Secret Manager and, while drafting, in the gitignored `.env.secrets`.

The executable input contract is [`.env.example`](../.env.example) plus
[`.env.secrets.example`](../.env.secrets.example). Copy them to the gitignored `.env` and
`.env.secrets`, then run `make deployment-check`. That preflight rejects every `PENDING`,
`PLACEHOLDER`, `REPLACE`, `CHANGEME`, `TBD`, `TODO` and reserved `.test` domain, and it treats
the Mode 5 registration as all-or-nothing: naming any one of its variables demands all of them
plus `PORTAL_SESSION_SIGNING_KEY`. A row marked `PENDING` below is therefore a row that will
stop a production command, by design.

Repository code and reusable infrastructure are ready. The dossier is deliberately incomplete
until the institution supplies the decisions below. A fictional institution is not production
evidence.

## 1. Deployment identity and owners

| Input | Required value | Approval or evidence |
|---|---|---|
| Institution | `PLACEHOLDER` | Executive sponsor |
| Installation name | `hrz9-<institution>-production` | Deployment owner |
| Deployment owner | Ashish Awasthi | Named person or team |
| Security owner | Ashish Awasthi | Named person or team |
| DNS and IAP owner | Ashish Awasthi | Named person or team |
| Operations owner | Ashish Awasthi | Named person or team |
| Evidence approver | Ashish Awasthi | Independent reviewer — **NOT independent; see the recorded deviation in the Doc1 dossier** |
| Incident channel | `PENDING` | Tested escalation route |
| Evidence-retention location | `PENDING` | Access-controlled record location |

**Recorded deviation: single-person ownership. DECIDED 2026-08-24.** Every owner role resolves
to one person, by explicit decision rather than by default staffing: the question was asked
directly and answered "all four are me for now." Two consequences are therefore accepted, not
merely disclosed, and stay accepted until a second person joins one of these roles: the evidence
approver is not independent, so self-approval does not satisfy the review rule for that row; and
there is no second custodian, so the incident channel and the emergency key-revocation path both
terminate at the same person. The identical decision is recorded in Doc1's dossier.

## 2. Cloud, residency and edge

| Input | Required value | Review rule |
|---|---|---|
| GCP organization and project | The reference deployment SHARES Doc1's project, decided by the apply rather than on paper. One consequence is load-bearing: a project belongs to exactly one regular VPC-SC perimeter, and the Doc1 stack owns it, so this stack configures none | Dedicated or explicitly approved shared project |
| Approved region | `us-central1` | Must be in `allowed_regions` |
| Allowed regions | `us-central1` | Security and legal approval attached |
| Parent origin | `PENDING` | Dedicated HTTPS origin, exact, no wildcard |
| DNS managed zone | `PENDING`, per Doc1's dossier | Change window recorded |
| Certificate authority | Google-managed certificate (Certificate Manager) | Managed certificate or approved equivalent |
| IAP OAuth client and JWT audience | `PENDING`; Terraform computes the exact IAP audience during the first apply and operators must not guess it | Edge authentication configured on the service |
| Terraform state backend | `PENDING` | GCS bucket plus installation-specific prefix; local state is rejected |

**Settled: the two systems co-locate in `us-central1`.** They once defaulted to different
regions, and this section used to record that as an open choice. It is not one any more.

The residency decision is the catalog's
[deployment region alignment](https://github.com/portable-genai/org-metadata/blob/main/docs/deployment-region-alignment.md)
record, taken 2026-08-23 and **REVISED 2026-08-24**: the launch set CO-LOCATES, and that region is
`us-central1`, not the `asia-southeast1` the first version named. Deploying any service outside it
is an exception needing the service named, the reason it cannot run in-region, a data-flow record
and approval. Read the revision, not only the original date: the two say different things.

**The defaults on this side follow that decision**, and had to be changed to do so. Both the
Terraform `region` and `allowed_regions` (`infra/terraform/variables.tf`) and the runtime
`config.py` preflight now default to `us-central1`. They previously defaulted to
`asia-southeast1`, which meant a default `terraform plan` against the live stack failed its own
residency validation — and failed looking like a working guard rather than a stale default, which
is the more expensive kind of wrong. The region remains a deploy-time input on both sides, so an
institution deploying in-country sets the region and the allowlist together and edits no code.

**What the revision retired.** The original record deferred a per-service availability check to
just before the apply, because it could not evidence that every managed service Doc1 binds is
available in `asia-southeast1`. Under `us-central1` that check is moot and is NOT carried forward;
it returns the moment a target region other than `us-central1` is chosen.

**What it costs, stated rather than absorbed.** `us-central1` satisfies no Asia-Pacific residency
regime. This deployment demonstrates that the residency MECHANISM works — an allowlist enforced at
`terraform plan` and again at app load, with a drift test — and does not demonstrate an in-country
APAC deployment. Any pitch citing it says which of the two it is showing.

Sign-off stays where it was. Owner: security owner. Status: `PENDING` sign-off against a recorded
position, no longer an unmade choice.

## 3. BFF service identity and key custody

The portal authenticates to Doc1's grant endpoint with `private_key_jwt` (RFC 7523). The
assertion is minted by
[`domain/bff_assertion.py`](../src/journey_portal/domain/bff_assertion.py) and signed through
[`ports/bff_credentials.py`](../src/journey_portal/ports/bff_credentials.py). In every managed
profile the signer is a non-exportable Cloud KMS key version, so the deployment names a VERSION
and never key material; the local profile keeps a generated key in a gitignored file and exists
only for offline dev, tests and the demo.

| Input | Required value | Review rule |
|---|---|---|
| BFF client id | `PENDING` (registered with Doc1 as a distinct service identity; goes in `PORTAL_DOC1_BFF_CLIENT_ID` and must equal what Doc1 registers) | Registered service identity |
| BFF authentication method | `private_key_jwt` | mTLS or `private_key_jwt` |
| Signature algorithm | `RS256` (Doc1 registers RS256 or ES256; this repo publishes and signs RS256) | Reviewed algorithm |
| Assertion audience | The EXACT Doc1 grant endpoint URL, compared as a string by Doc1's verifier | Exact endpoint, never an origin or prefix |
| Assertion lifetime and skew | 60 seconds, matching Doc1's registered maximum; the minter refuses a longer one at construction | Reviewed bounded values |
| Active signing key version | `PENDING` (fully qualified `projects/.../cryptoKeyVersions/N`) | Exact KMS version, non-exportable, HSM protection level |
| Active `kid` | `PENDING` (goes in `PORTAL_BFF_SIGNING_KID`; it is what the JWK set and every assertion header carry, and what Doc1 pins) | Stable across restarts |
| Accepted verification keys and overlap window | `PENDING` (`PORTAL_BFF_ACCEPTED_PUBLIC_JWKS`, empty except during a rotation) | Bounded overlap window |
| Rotation dates and owner | owner PENDING; dates `PENDING` | Rehearsed without token outage |
| Emergency revocation procedure | `PENDING` (KMS version disable, republish the JWK set, evidence capture) | Tested emergency path |
| Session signing key | `PENDING` (`PORTAL_SESSION_SIGNING_KEY` in Secret Manager; keys the CSRF tokens and the hashed session binding) | Secret Manager, survives restart |

**Why the rotation dates matter to Doc1 and not only here.** Doc1's dossier section 5 tracks the
JWKS rotation dates and the revocation procedure by reference to this file: "Rotation dates and
owner" and "Emergency revocation procedure" below. Filling them here fills them there too.

## 4. JWKS publication

`GET /.well-known/doc1-bff-jwks.json` serves the public keys from the signing-key port. It is
unauthenticated and cacheable on purpose: a JWK set is public key material, it is what a relying
party fetches BEFORE it holds any credential of ours, and a login wall in front of it would
prevent the registration it exists for. The route is exempted from the tenant-policy and
principal requirements alongside `/healthz`, and it still receives the framing, nosniff and
referrer headers.

| Input | Required value | Review rule |
|---|---|---|
| JWKS URL | `PENDING` (the exact path must match what is recorded in Doc1's dossier; changing it breaks a reviewed registration) | Stable, public, no authentication |
| Cache lifetime | `public, max-age=300` | Short enough to propagate a rotation inside the overlap window |
| Published members | `kty`, `use`, `alg`, `kid`, `n`, `e` only. The port refuses to construct a published key carrying `d`, `p`, `q`, `dp`, `dq`, `qi` or `k` | No private key material, ever |
| Egress allowance for Doc1 | `PENDING` (Doc1's environment must be allowed to fetch this URL) | Restricted to approved hosts |

## 5. Session binding and user-intent evidence

Doc1's broker refuses a grant unless the host proves a real user, in a real session, asked for
it. The portal enforces every one of those rules on its own side FIRST, before any credential is
minted and before the broker is called, so a forged request never consumes a JTI, a rate-limit
slot or a log line on Doc1. Doc1 then re-validates all of it: the duplication is deliberate,
because a proof only the far side checks is a proof the near side can be tricked into signing.

| Control | How it is enforced here | Evidence |
|---|---|---|
| Exact host origin | `Origin` compared exactly against `PORTAL_PUBLIC_ORIGIN`; a prefix or suffix comparison would accept `https://<the parent origin>.attacker.example` | `tests/test_doc1_grant_route.py`, `tests/test_csrf_and_host_proof.py` |
| Fetch Metadata | `Sec-Fetch-Site` must be `same-origin`; a `navigate` mode or `document` destination is refused | Same |
| CSRF | A stateless token bound to the session binding AND to the exact method and path, 90-second lifetime, constant-time comparison, no server-side state. Doc1's `api/csrf.py` is the reference | `tests/test_csrf_and_host_proof.py` |
| Session source subject | The proof carries the PORTAL-VERIFIED principal's subject, never anything the client sent. Doc1 requires it to equal the subject it independently derives from the subject token | `tests/test_doc1_grant_route.py` |
| Session binding | A SHA-256 hex digest keyed on the session signing key over the length-prefixed subject and tenant, so Doc1 can correlate a session without ever receiving an identifier | Same |
| User-intent id | A fresh opaque value per request, matching Doc1's `^[A-Za-z0-9._~:-]{16,256}$` | Same |
| Audit | Every decision on the grant path, allowed or refused, appends one content-free event to the hash-chained access ledger | `domain/audit.py`, ledger integrity route |

**Open input: the subject must be the same person on both sides.** Doc1 compares
`session_source_subject` against the subject it derives from the subject token. That holds only
if the portal's verified principal subject and the Google `sub` in the subject token are the same
string. Under IAP the portal's subject is whatever the bound identity adapter returns. Confirm
the exact form before the first grant, and record it here. Status: `PENDING`. Owner: security
owner.

## 6. The end-user subject token

This is the one part of the Mode 5 exchange the portal cannot yet produce, and the reason the
managed subject-token adapter refuses by name rather than guessing.

Doc1's reviewed installation accepts an OIDC ID token under the Google profile: issuer
`https://accounts.google.com`, `aud` and `azp` both equal to a DEDICATED Google OAuth client used
for nothing else, and `hd` pinned to the Workspace domain. Two things must exist before the
portal can hand the broker one:

| Input | Required value | Review rule |
|---|---|---|
| Dedicated Google OAuth client id | `PENDING` (the same client Doc1's dossier section 5 records as `PENDING`; it is a single input that unblocks both sides) | Distinct broker audience, used for nothing else |
| Portal-side OIDC session holding that client's ID token | `PENDING` (Authorization Code plus PKCE at `accounts.google.com` against that client, with the ID token held server-side for the session the portal already verified) | Server-side only; the browser never holds it |
| Hosted domain pin | `PENDING` | Issuer-qualified links, never email |

Until both land, `POST /v1/doc1/embed/grant` answers 503 under every managed profile, with a
message naming exactly these rows. That is the honest posture: a portal that guessed here would
send a token from the wrong client, and the exchange would fail at Doc1 with a message pointing
at the wrong side of the boundary.

## 7. Promotion and evidence pack

The evidence pack for a Mode 5 sign-off must contain, in addition to the standard Hrz9 items in
[`runbook.md`](runbook.md):

- the published JWK set fetched from the production URL, and the `kid` it carries matched against
  the KMS key version recorded in section 3;
- a cross-repo verification record: an assertion minted by the deployed portal accepted by Doc1's
  verifier, and a replay of the same assertion refused;
- adversarial grant evidence: cross-site origin, missing CSRF token, a CSRF token from another
  session, and a navigation-destination request, each refused with NO outbound call to Doc1;
- key rotation and emergency revocation rehearsal, including the overlap window;
- the security owner's sign-off on the recorded residency decision in section 2, plus the per-service availability check that record names;
- named approvals from the deployment, security, operations and evidence owners.

## 8. Completion decision

| Gate | Status | Evidence |
|---|---|---|
| Portal Mode 5 code | Ready | `private_key_jwt` minting against a signing-key port with local and KMS adapter families, the JWKS route, CSRF plus exact-origin plus Fetch Metadata enforcement, and a broker client building the proof from the verified principal. The local gate is green |
| Cross-repo agreement with Doc1 | Ready | `tests/test_cross_repo_doc1_private_key_jwt.py` verifies a portal-minted assertion against Doc1's ACTUAL `PrivateKeyJwtVerifier`, imported from the sibling checkout and run in its own virtualenv, with the replay, tamper, expiry, wrong-audience and unregistered-client negatives all refused |
| Named institution inputs | PARTIAL | Section 2 is settled by the apply: the project is shared with Doc1, the region is `us-central1`, the IAP client and its audience exist and Terraform computed the audience rather than an operator guessing it, and the state backend is configured. Still outstanding: section 1's incident channel and evidence-retention location, and section 3's key custody and rotation dates |
| Subject-token source | BLOCKED | Section 6: the dedicated Google OAuth client id and a portal-side OIDC session. Both are also the last Mode 5 blocker on Doc1's own row |
| Residency decision | RECORDED, sign-off PENDING | Section 2: the region is a deploy-time input validated against `allowed_regions` on both sides. The choice is the catalog's deployment region alignment decision, recorded 2026-08-23 and **revised 2026-08-24**: co-locate in `us-central1`, deviation by named exception. The revision also retired the per-service availability check the first version owed, so that is no longer outstanding. What remains is the security owner's sign-off |
| Controlled pre-production apply | DONE, and not a production service | Applied and serving. What is live, and how far each claim is proved rather than merely configured, is the catalog's deployment record; this dossier does not keep a second copy. Not evidenced on this stack: VPC-SC (the Doc1 stack owns the project's one perimeter), audit retention applied but unlocked, no HA, no rehearsed rollback |

The remaining work on this side is inputs, not code. Every `PENDING` row names exactly what is
needed and who owns it, which is the whole point of recording them rather than inventing values.

**This dossier records inputs and decisions. It does not record deployment state**, which has one
home in the catalog's `docs/deployment-status.md`. The two disagreed once, and the stale one was
the one a buyer reads.
