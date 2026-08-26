# Hrz9 common-base practices audit

Scope: BFF, both shell toolchains, identity/proxy boundary, containers, CI, demos, adoption
material and Terraform. Run commands from the repository root.

Verdicts are repository-specific. `N-A` includes a one-line applicability reason. `PARTIAL` is not
treated as a pass.

## A. Architecture

| Check | Verdict | Evidence |
|---|---|---|
| A1 pure domain | PASS | `src/journey_portal/domain/`; `rg -n "^(from\\|import) (fastapi\\|httpx\\|pydantic\\|google)" src/journey_portal/domain` returns no imports. |
| A2 runtime protocols | PASS | `ports/upstream.py` is a runtime-checkable Protocol; identity consumes the commons `IdentityPort`; `tests/test_contract_parity.py`. |
| A3 explicit profiles | PASS | Exact `local`, `gcp`, `platform`, `onprem` maps in `config.py`; unknown profiles fail; local works offline; on-prem fails fast. |
| A4 constructor convention | PASS | Every adapter accepts one `Settings`; the parity test constructs every profile. |
| A5 lazy cloud imports | PASS | Google imports occur inside managed adapter methods; the SDK-free gate imports all profiles. |
| A6 contract drift guard | PASS | `tests/test_contract_parity.py` plus `scripts/portability_demo.py` assert profile coverage and protocol conformance. |
| A7 kernel/vertical boundary | PASS | `domain/models.py` is the stable value-object kernel; `catalog.py` and `identity_injection.py` contain Hrz9 behavior; `ARCHITECTURE.md` and `docs/ADOPTING.md` name the boundary. |
| A8 consume horizontals | PASS | Hrz9 hosts verticals and contains no guardrail, RAG, registry, evaluation engine or observability implementation to duplicate. The `platform` access-audit adapter is a thin authenticated delegate to Hrz5 `/v1/audit`; deterministic domain mapping keeps the payload content-free. |

## B. Determinism and LLM boundary

| Check | Verdict | Evidence |
|---|---|---|
| B1 deterministic decisions | N-A | Hrz9 has no model, score, eligibility or consequential decision engine. Routing and header plans are deterministic domain functions. |
| B2 citations | N-A | Hrz9 generates no claims or narrative. Embedded applications own citations. |
| B3 maker-checker | N-A | Hrz9 makes no consequential output; it composes Hrz7 for review. |
| B4 policy in config | PASS | Journey composition, upstreams, timeout, region, runtime sizing and policy origins are settings/Terraform inputs with override and rejection tests. |
| B5 open taxonomy | N-A | Hrz9 has no regulated category taxonomy or engine keyed by category. |

## C. Security

| Check | Verdict | Evidence |
|---|---|---|
| C1 server identity | PASS | `api/security.py` discards browser persona and Authorization; every API proxy route depends on the verified principal; spoof tests and eval fail-green test pass. **The commons CLAIM half is deliberately NOT adopted here, and `tests/test_iap_claim_half_divergence.py` proves why by running both over the same claim sets (2026-08-26).** Forty-five repositories now end `resolve()` with one `hex_service_kit.federation.principal_from_iap_claims` call, and this was the obvious next one, because it is the only deployment that actually configures a reviewed domain map (`PORTAL_TENANT_DOMAINS`, parsed in `config.py`). The map is not the part that disagrees. **The blocker is the machine caller, and it empties a load-bearing tenant.** `FederationPolicy.tenant_for` short circuits on `machine=True` and returns `machine_tenant`, one string for every machine caller, BEFORE `domain_tenants` is consulted at all; this deployment maps a service account's own `<project>.iam.gserviceaccount.com` domain onto a reviewed tenant, so under the commons every mapped machine caller resolves to no tenant. Fail-closed and closed for a whole population, and invisible to an offline gate because the local profile never constructs this adapter. Filling `machine_tenant` in is not the fix: it is one string, so it would give a caller from an unreviewed project the same tenant as a reviewed one, and the divergence module asserts exactly that. **The second disagreement runs the other way**: `tenant_for` consults the reviewed map for the asserted domain AND the mail domain and takes the first mapped, where this adapter consults `hd` if present and otherwise the mail domain, so a caller whose `hd` is present but unmapped and whose mail domain is mapped goes from no tenant to a mapped one. Both belong in the kit's backlog as a missing `machine_tenants` map, not in a pull request that quietly changes who this service serves. **The transport half WAS missing here and is now closed.** `adapters/gcp/identity.py` still carried its own three literals for the assertion header, the issuer and the key set while `domain/identity_injection.py` had taken them from the commons since tier 3 landed; nothing could notice, because a literal always agrees with itself. They are rebound, and the guard asserts the SOURCE rather than the value, since an equality assertion is exactly what a fresh copy satisfies: RED first, `AssertionError: x-goog-iap-jwt-assertion is re-declared rather than rebound`. |
| C2 object/tenant authorization | N-A | Hrz9 owns no business objects or data store. Each embedded API remains responsible for object and tenant authorization after re-verifying identity. |
| C3 redact first | N-A | Hrz9 has no agent/model boundary and does not parse or persist proxied bodies. |
| C4 jurisdiction PII packs | N-A | Hrz9 performs no PII detection or model safety scoring. Hrz1 and embedded applications own this control. |
| C5 fail closed | PASS | Unknown profiles/regions, insecure managed upstreams and malformed origins fail; local bind is loopback; wildcard policy is managed-profile-invalid. Every external host resolves to one tenant policy, the verified tenant must match, and an unapproved Origin is rejected before route side effects. |
| C6 surface headers | PASS | The BFF selects CSP frame ancestors and exact CORS response from the resolved tenant policy. Both production static servers consume the same host-bound registry and emit `frame-ancestors 'none'` for an unknown or malformed host. Nosniff, Referrer-Policy and managed-profile HSTS remain mandatory. |
| C7 authenticated S2S | PASS | Managed catalog validation requires HTTPS; `GcpUpstreamClient` replaces Authorization with an audience-bound workload-identity ID token; embedded services are internal-only with BFF-only invoker IAM. |
| C8 owned login | N-A | Hrz9 owns no browser login or session protocol. The named deployment uses IAP and verifies its assertion audience. |
| C9 tamper-evident audit | PARTIAL | Local forwarding and every tenant embed-policy allow/deny assessment write keyed pseudonymous metadata to a mode-0600 SQLite SHA-256 chain and verify it against an HMAC-protected count/head checkpoint. Managed delivery synchronously writes the same bounded events to Cloud Logging and fails closed on failure. Terraform routes evidence to a regional bucket and can irreversibly lock retention after approval. Named live lock/apply evidence remains. |
| C10 no committed secrets | PASS | Config holds env/secret names only; `.env.secrets.example` contains only a fail-closed placeholder; the real mode-0600 file is ignored; release and live CI use WIF. `rg -n "(BEGIN .*PRIVATE KEY\\|AIza[0-9A-Za-z_-]{30,})" .` is clean. |

## D. Supply chain and CI

| Check | Verdict | Evidence |
|---|---|---|
| D1 locked installs | PASS | Python build/runtime lockfiles pin resolved commits and packages; UI lockfiles use `npm ci`; BFF container installs both locks and the project with `--no-build-isolation --no-deps`. |
| D2 digests, actions, audits | PASS | Base images and Actions are SHA-pinned; Dependabot covers pip/npm/docker/actions; Python audits are hard gates. Patched `sharp`, `postcss`, `@modelcontextprotocol/sdk` and `uuid` overrides remove the reviewed high-severity findings, and the npm gate now rejects every high or critical finding without exceptions. Node packages are absent from both runtime images. |
| D3 offline fork gate | PASS | CI runs lint, format, types, unit/contract tests, eval, API demo and portability evidence under local with no org secret. |
| D4 minimal containers | PASS | BFF/RM/Ops use multi-stage digest-pinned builds, dedicated uid 10001 and probes; shell runtimes contain only Python stdlib and static output. |
| D5 residency/sovereignty | PARTIAL | Region/allowlist, optional Org Policies, private ingress, regional evidence, regional CMEK, and VPC-SC dry-run are defined and offline-tested. Enforcement is deliberately rejected while unrestricted Cloud NAT remains; no named apply proves live controls. |

## E. Quality and evals

| Check | Verdict | Evidence |
|---|---|---|
| E1 offline eval | N-A | Hrz9 is non-agentic, so Hrz4 is not a promotion authority for a model. It still runs deterministic journey, routing, identity and tenant-policy isolation metrics in CI. |
| E2 safety false-green | N-A | Hrz9 has no PII safety metric. Its analogous identity and tenant-policy isolation metrics have strict 0.99 thresholds and tests proving each can fail. |
| E3 fictional fixtures | PASS | Eval/demo identities use `example.test`/`bank.example` and are explicitly synthetic; live walkthrough data remains audience/public-record supplied. |

## F. Demo and anti-rot

| Check | Verdict | Evidence |
|---|---|---|
| F1 one-command demo | PASS | `make demo` is offline; `scripts/demo_walkthrough.py` is presenter-paced/resumable; `make journeys` starts real sibling services. |
| F2 anti-rot | PASS | CI runs the real BFF contract self-test plus production-built RM/Ops shells in headless Chromium, using stable `data-demo` hooks and asserting live journey, identity, tab and iframe state. |
| F3 bounded portability | PASS | `scripts/portability_demo.py` executes two-framework channel, identity isolation, exact profile parity and fail-fast on-prem seams, then states unproved dimensions. |
| F4 local and GCP proved as a PAIR | **FAIL** | First F4 score in the fleet, and it is red on purpose. `make e2e-pair` (`e2e/pairing.py`, `e2e/pair_report.py`) compares the deterministic half of the dossier captured from the console's own `POST /v1/cdd` on both targets and exits non-zero on divergence; `tests/test_pairing.py` gates it offline, proving each class of policy divergence is caught and each declared reduction tolerated. Run live on 2026-08-25 against `localhost:3000` and the deployed RM origin, it found **12 divergences over 18 compared fields**. The load-bearing ones: the deployment performed NO sanctions/PEP screening (`screening.present` true vs false) while the laptop screened six lists, and nothing on either surface said so; the same subject and the same evidence produced a different verdict (`rating.band` medium vs low, `rating.score` 0.45 vs 0.1) off entirely different scorecard factors; UBO resolution returned an owner locally and none on the deployment; and every managed citation title decayed into its own opaque document id (8 of 8 on the source-of-wealth panel), so a cited source reads as `doc-c9dba9861a1f` rather than as the bank statement. The two profiles are not running the same case, which is what two runs recorded side by side had been concealing. The command is the deliverable here; the green is not owed until those gaps close. Evidence: `e2e/out/pair/comparison.json`. |

## G. Documentation and adoption

| Check | Verdict | Evidence |
|---|---|---|
| G1 authority order | PASS | `AGENTS.md`/`AGENTS.md` declare Spec, Architecture, Compliance, README; shipped cloud and Doc1 mode statements were reconciled. |
| G2 control mapping | PASS | `COMPLIANCE.md` maps principles/rules to evidence and includes an adopter-owned regulator crosswalk. |
| G3 fork path | PASS | `docs/ADOPTING.md` defines ownership/decisions; `scripts/rename_fork.py` supports dry-run then clean-clone apply. |
| G4 Retired | N-A (retired) | Retired practice. Releases are tracked by git tag and the `pyproject.toml` version. |
| G5 role FAQs | PASS | `docs/faq/` covers feature, security, compliance, portability and adoption roles and names adjacent catalog owners. |
| G6 contribution extension path | PASS | `CONTRIBUTING.md` lists the complete port/adapter touch set and points to parity failure. |
| G7 Markdown/mermaid | PASS | The repository-wide em-dash scan is clean; existing diagrams remain syntactically bounded and docs links are repository-relative. |

## Surviving findings and external completion gates

- C9: a named deployment must apply and approve the irreversible audit-retention lock.
- D5: live region, CMEK and Org Policy evidence requires institution authority. VPC-SC
  enforcement additionally requires a restricted-egress replacement for unrestricted Cloud NAT.
- Live deployment proof still requires project/IAM, immutable application images, IAP/WIF,
  DNS/TLS, secrets, users, alert channels and apply approval.
- Per-hop OBO remains deliberately deferred.

## Commands run

```bash
make check
make demo-selftest
make portability
python scripts/demo_browser_selftest.py
cd ui-rm && npm ci && npm run lint && PORTAL_STATIC_EXPORT=1 npm run build
cd ui-ops && npm ci && npm run lint && npm run build
terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```
