# COMPLIANCE: Hrz9 Journey Portal Shell

How this repo maps to the catalog's General Principles (P-01..P-13) and Dependency Rules (R1..R8).
The portal is a control-plane host/shell with no model. It does not inspect customer payload PII,
but it verifies identity and retains keyed pseudonymous actor and tenant references for access
evidence. Those references remain personal data. Several model principles are absent by design,
while the load-bearing controls are identity, zero-trust, residency, auditability, reversibility
and eval-gated promotion.

## Principles

| ID | Principle | How this repo satisfies it |
|---|---|---|
| P-01 | Hybrid on-prem + GCP / VPC-SC | `infra/terraform` deploys private Cloud Run services behind IAP and defines an explicit dry-run-first VPC-SC perimeter; `onprem` remains a fail-fast seam documented in `docs/onprem-migration.md`. Live perimeter evidence remains adopter-owned. |
| P-02 | No vendor lock-in (ports & adapters) | Hexagonal: pure-stdlib domain, typed ports, `local`/`gcp`/`platform`/`onprem` adapter families; switching the stack is a one-line `PORTAL_PROFILE` change with no domain edits. The parity and portability checks prove the bounded seam. |
| P-03 | Single-region residency | Application and Terraform settings validate the same approved region set; Terraform offers an explicit, authority-gated resource-location Org Policy and binds a regional CMEK to Cloud Run and the regional audit bucket. |
| P-04 | Minimise data to the model | n/a (no model). The BFF does not parse, log, or persist request/response bodies. The access ledger retains only deployment-keyed actor/tenant references and bounded route metadata, treated as pseudonymous personal data. |
| P-05 | Grounding over fine-tuning | n/a (no model, no training). |
| P-06 | Human-in-the-loop / maker-checker | Inherited and composed: the portal takes no consequential action of its own, and it composes the **Hrz7 Human-Review Console** into the Ops journey so the maker-checker queue is one click inside the portal. |
| P-07 | Auditable & explainable | The verified identity becomes each embedded app's audit actor. Every host/tenant/origin assessment records a content-free allow/deny event, and `/v1/embed-policy` exposes the applied policy evidence. Local forwarding writes keyed, pseudonymous metadata to an append-only SQLite SHA-256 chain with an HMAC-protected count/head checkpoint. The production `platform` profile synchronously maps the same bounded event to Hrz5 `/v1/audit` with audience-bound workload identity and fails closed on delivery failure. |
| P-08 | Eval-gated promotion | `eval/run_eval.py`: `journey_integrity`, `identity_isolation`, `routing_correctness`, and `tenant_policy_isolation` (security metrics at 0.99); a `--mode gate` runner reconciles with the Hrz4 authority. |
| P-09 | Defense in depth / zero trust | The core of this repo. The portal never trusts a browser-asserted identity (strip-and-inject); each embedded app **re-verifies** the identity itself; exact host-to-tenant policy rejects identity and Origin mismatches before proxy side effects; CSP `frame-ancestors` and CORS are selected from that reviewed policy; local remains loopback-only. |
| P-10 | Resilience & graceful degradation | A down embedded app degrades to an iframe error while other apps remain routed; health probes, bounded scaling, rollback digests and fail-fast placeholders are explicit. |
| P-11 | Cost & latency control | n/a (no model tokens). The proxy forwards identity encoding and streams bytes without re-compression. |
| P-12 | Reversibility / documented exit | `onprem` profile placeholders for identity and egress; the exit path is documented in `docs/onprem-migration.md`. |
| P-13 | Fair, consented marketing | n/a (not a marketing surface). |

## Dependency rules

| Rule | Applies? | Notes |
|---|---|---|
| R1 (PII -> Hrz1 Guardrail) | n/a to the portal | The portal reads no bodies and calls no model. Each embedded app depends on Hrz1 itself. |
| R2 (production -> Hrz5 audit) | Met | Terraform selects the `platform` access-audit adapter, supplies the exact Hrz5 HTTPS origin, and sends keyed pseudonyms plus bounded route metadata to `/v1/audit` with an audience-bound workload token. Embedded-app business actions remain audited by their owning apps. |
| R3 (RAG -> Hrz2) | n/a | No RAG. |
| R4 (register in Hrz3) | Should | The deployed portal surface should register in the Hrz3 registry (roadmap). |
| R5 (promotion -> Hrz4 gate) | Yes | `eval/run_eval.py --mode gate` reconciles with the Hrz4 promotion authority. |
| R6 (Rsk3 intake) | Should | New surface; passes the architecture/requirements validator at intake. |
| R7 (marketing -> Mkt6) | n/a | Not a marketing output. |
| R8 (escalation -> Hrz7) | n/a to the portal; composed | The portal raises no `requires_human_review` of its own. It hosts the Hrz7 console so escalations from the embedded apps are actioned inside the same journey. |

## The security invariant this repo adds

Beyond inheriting the catalog posture, the portal introduces one new load-bearing control: **a
browser-asserted identity never reaches an embedded app.** It is implemented in
`domain/identity_injection.py`, unit-tested in `tests/test_identity_injection.py`, and scored as
`identity_isolation` (threshold 0.99) in the eval gate, with a not-falsely-green test proving the
metric fails when a spoofed identity leaks.

## The service-identity controls the Doc1 Mode 5 half adds

The portal holds the only service identity in Doc1's cross-origin embedded grant, so four further
controls are load-bearing:

| Control | Where it lives | Evidence |
|---|---|---|
| The signing key is never a value anybody can paste, and never leaves its custody | `ports/bff_credentials.py` plus the KMS adapter, which names only a key VERSION | `tests/test_bff_signing_key.py`; the port refuses to construct a published key carrying a private JWK member |
| An assertion this portal mints is accepted by the far side, and its negatives are refused | `domain/bff_assertion.py` | `tests/test_cross_repo_doc1_private_key_jwt.py`, which runs Doc1's ACTUAL verifier from the sibling checkout |
| A forged grant request is refused before any credential exists | `api/doc1_grant.py`, in that order deliberately | `tests/test_doc1_grant_route.py` asserts the recording upstream saw NO call |
| The user-intent evidence comes from the verified principal, never from the client | `domain/doc1_broker.py` | `tests/test_csrf_and_host_proof.py`, `tests/test_doc1_grant_route.py` |

Deployment inputs that are still outstanding, and the reason each one blocks, are recorded row by
row in [`docs/named-deployment-dossier.md`](docs/named-deployment-dossier.md). They are recorded
rather than invented: the managed subject-token adapter refuses by name instead of guessing a
credential.

## Adopter-owned regulator crosswalk

The adopter's compliance owner maps these controls to the institution's current obligations and
records scope, interpretation, evidence owner and approval date. The starter row is intentionally
not a legal conclusion:

| Regulation/control | Hrz9 evidence to assess | Adopter owner | Status |
|---|---|---|---|
| Institution technology-risk access, change and audit controls | `infra/terraform/`, `docs/runbook.md`, `docs/practices-audit.md` | To be named | Adopter review required |
