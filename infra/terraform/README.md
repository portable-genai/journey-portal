# Complete `journey-portal` journey deployment

This stack provisions the reusable, input-independent production shape. It does not claim a live
deployment. A named institution must still provide a target project, reviewed image digests,
IAP registration, DNS authority, approved users, secrets, and apply approval.

## Resources

- private Cloud Run services for the BFF, RM shell, Ops shell, and every configured embedded
  application's UI and API;
- separate UI/API service accounts and Secret Manager grants, service-to-service invoker grants,
  and authenticated HTTPS calls from the BFF;
- a dedicated VPC/subnet with Private Google Access and Direct VPC `ALL_TRAFFIC` egress from the
  BFF so its calls can reach internal-only Cloud Run UI/API services;
- a logged Cloud NAT/router scoped to that subnet, allowing the BFF verifier to retrieve IAP's
  public signing keys without assigning the service an external IP;
- serverless NEGs, three IAP-enabled backends, host and path routing, managed TLS, a global
  address and optional Cloud DNS records;
- deploy-time region validation against the residency allowlist, image-digest and sizing
  validation, plus a host-bound tenant
  policy registry carrying frame ancestors and CORS origins to the BFF and both shells;
- opt-in resource-location and service-account-key Org Policies;
- one regional CMEK bound to every Cloud Run revision and the audit bucket;
- an explicit VPC-SC dry-run perimeter; enforcement fails closed until unrestricted NAT is
  replaced by reviewed restricted egress;
- regional access evidence with 180-day default retention, opt-in lock, and alerts for IAP
  denials, service-account keys, perimeter denials and CMEK changes.

There is no public Cloud Run invoker. Shell and BFF ingress accepts only the external load
balancer. Embedded UI/API ingress is internal-only and grants invocation only to the BFF service
account. Each embedded UI and API has a distinct runtime identity, environment map and secret
map; a secret grant for one surface does not authorize the other.

Because the BFF sends all traffic through Direct VPC egress, its dedicated subnet uses Cloud NAT
for public destinations such as IAP's signing-key endpoint. NAT provides outbound translation, not
inbound reachability, and logs all translations/errors. Institutions that prohibit general
outbound access should replace this boundary with an approved egress proxy or automated internal
IAP-key cache before apply; a live key-fetch and assertion-verification check remains mandatory.

## Validate without cloud credentials

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```

The mocked tests plan the complete edge and private service topology, prove a region outside the
residency allowlist is refused, and prove a reviewed allowlist extension deploys to the selected
region.
They do not fabricate provider apply evidence.

## Apply a named environment in two stages

The preferred input path separates `.env` from `.env.secrets`, validates every required value,
and renders only non-secret Terraform inputs:

```bash
cp .env.example .env
cp .env.secrets.example .env.secrets
chmod 600 .env.secrets
python scripts/deployment_config.py check
python scripts/deployment_config.py render
python scripts/deployment_config.py terraform -- init
python scripts/deployment_config.py terraform -- plan -out=reviewed.tfplan
```

The runner rejects user-supplied `-var`, `-var-file`, backend overrides, local state and competing
automatic or override files. It sanitizes ambient Terraform variables, initializes only the
reviewed GCS bucket/prefix, verifies the resulting backend metadata, and uses one fixed ignored
generated input file.

Each embedded app entry must declare `ui_build_base_path`: `/agent` for `cdd-sow-research` and `/apps/<id>`
for the other five apps. This is a reviewed build-time image contract, not a runtime environment
override. Build and test each UI for that path before recording its immutable digest.
UI/API plain and secret environment maps cannot overlap within a container. Every surface rejects
Cloud Run-managed variables and Terraform-owned profile/audience names; only each API's required
managed profile key is accepted in its plain API environment map.
The BFF separately requires `portal_audit_hmac_secret` and an exact numeric
`portal_audit_hmac_secret_version`. Terraform grants only the BFF runtime access and mounts the
value as `PORTAL_AUDIT_HMAC_KEY`; the secret value never enters generated inputs or Terraform
state.
`tenant_embed_policies` is non-secret but security-critical. Every RM/Ops hostname must resolve to
exactly one policy, each managed policy names an exact tenant rather than `*`, and all frame/CORS
origins are exact HTTPS origins. The BFF and both shells receive the same `jsonencode`d registry.

Stage one creates the edge but grants no user or group IAP access. Copy the exact
`computed_portal_iap_audience` output into `iap_jwt_audience`, add the approved `iap_members`,
then save, review and apply a second plan. Terraform rejects any access grant if the configured
audience differs from the backend audience computed by the provider. Never guess or precompute
the numeric backend-service identifier.

Use encrypted, access-controlled remote state. IAP OAuth secrets are sensitive but Terraform still
stores them in state. Prefer a provider-supported managed OAuth client when the institution
supports one.

`apply_org_policies` and `lock_audit_bucket` default false. Project Org Policy changes need
separate authority, and locking the 180-day retention is irreversible. `vpc_sc_enforced=true` is
currently rejected because the BFF still uses unrestricted Cloud NAT for IAP key retrieval. Keep
the perimeter in dry-run until an approved restricted egress design replaces NAT. Read
[MIGRATION.md](MIGRATION.md) before adopting an existing BFF.

The CMEK rotation period uses the Cloud KMS duration format and must be between 86,400 seconds
(24 hours) and 3,153,600,000 seconds, with at most nine fractional digits.

## External completion blockers

- target project, billing/quota and organization-policy authority;
- Artifact Registry images built, scanned, signed and available by digest;
- IAP OAuth/WIF registration and approved users/groups (the exact audience is emitted by stage
  one);
- DNS zone ownership and managed-certificate domain validation;
- Secret Manager entries and least-privilege data-service permissions for each embedded app,
  plus an exact BFF audit-HMAC secret version;
- reviewed notification channels, retention, backup/restore and incident ownership;
- controlled two-stage apply, unauthenticated denial, authorized RM/Ops journeys, proof that the
  BFF reaches each internal-only destination through Direct VPC egress, proof that its verifier
  retrieves current IAP signing keys through logged Cloud NAT, rollback and browser evidence.

`observability_url` is the exact `agent-observability` HTTPS origin; `observability_audience` is the independently
reviewed audience `agent-observability` verifies. The BFF runs the `platform` profile and acquires an
audience-bound workload token before posting each content-free access event. The adopter must
grant the portal service account Cloud Run invoke access and enroll it in `agent-observability`'s allowed-caller
policy. Per-hop OBO and tenant-specific issuer/audience variants remain deferred.
