# Complete `journey-portal` journey deployment

This stack provisions the reusable, input-independent production shape. It does not claim a live
deployment. A named institution must still provide a target project, reviewed image digests,
IAP registration, DNS authority, approved users, secrets, and apply approval.

## Resources

- private Cloud Run services for the BFF, one per persona shell the deployment names in `shells`,
  and every configured embedded application's UI and API;
- separate UI/API service accounts and Secret Manager grants, service-to-service invoker grants,
  and authenticated HTTPS calls from the BFF;
- a dedicated VPC/subnet with Private Google Access and Direct VPC `ALL_TRAFFIC` egress from the
  BFF so its calls can reach internal-only Cloud Run UI/API services;
- a logged Cloud NAT/router scoped to that subnet, allowing the BFF verifier to retrieve IAP's
  public signing keys without assigning the service an external IP;
- serverless NEGs and one IAP-enabled backend for the BFF plus one per shell, host and path
  routing per shell, managed TLS covering every shell hostname, a global address and optional
  Cloud DNS records;
- deploy-time region validation against the residency allowlist, image-digest and sizing
  validation, plus a host-bound tenant
  policy registry carrying frame ancestors and CORS origins to the BFF and every shell;
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
residency allowlist is refused, prove a reviewed allowlist extension deploys to the selected
region, and plan a third persona shell beside the two the reference deployment publishes while
refusing each way a shell list can be wrong.
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

The apps a deployment can name are the keys of `local.embedded_app_managed_env` in
`embedded_apps.tf`. Each entry names the profile variable that app's API reads, which the deployment
must set to `gcp` or `platform`, and the IAP audience variable Terraform injects. An app the journey
catalog knows but that map does not name is refused at plan, and becomes deployable by adding its
entry there and in the renderer's `_MANAGED_ENV_BY_APP`; a contract test holds the two equal.
Each embedded app entry must declare `ui_build_base_path`: `/agent` for `cdd-sow-research` and `/apps/<id>`
for every other app. This is a reviewed build-time image contract, not a runtime environment
override. Build and test each UI for that path before recording its immutable digest.
UI/API plain and secret environment maps cannot overlap within a container. Every surface rejects
Cloud Run-managed variables and Terraform-owned profile/audience names; only each API's required
managed profile key is accepted in its plain API environment map.
The BFF separately requires `portal_audit_hmac_secret` and an exact numeric
`portal_audit_hmac_secret_version`. Terraform grants only the BFF runtime access and mounts the
value as `PORTAL_AUDIT_HMAC_KEY`; the secret value never enters generated inputs or Terraform
state.
`tenant_embed_policies` is non-secret but security-critical. Every shell hostname must resolve to
exactly one policy and every policy host must be a routed shell hostname -- the check runs both
ways, so a new host cannot be routed without being registered or registered without being routed.
Each managed policy names an exact tenant rather than `*`, and all frame/CORS origins are exact
HTTPS origins. The BFF and every shell receive the same `jsonencode`d registry.

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

## Adding a shell host

A shell is one journey on one hostname: a Cloud Run service running an image built for that
journey, its serverless NEG, its IAP-enabled backend service, a host rule and path matcher on the
url map, a domain on the managed certificate, an optional DNS record, an IAP grant per approved
member and a rollback component. All of it derives from one ordered list, `var.shells`
(`shells.tf`), whose entries are `{journey, image, domain}`. A fourth persona is an entry; no
resource in this stack names a journey. The deployment-side variable is `DEPLOY_SHELLS_JSON`.

The stack refuses, at plan, a shell whose journey the catalog (`config/journeys.yaml`) does not
define, two shells on one hostname, one journey served by two shells, and -- the one worth
explaining -- a shell **none of whose journey's apps are in `embedded_apps`**. The BFF drops a
journey with no mounted app, and the React shell falls back to the FIRST journey in the catalog
when the one it was built for is absent, so that host would serve a different persona's journey
and look perfectly healthy. `terraform test` covers each refusal.

To add one:

1. **Build the image for that journey.** The journey is compiled in, not configured: Next.js
   inlines `NEXT_PUBLIC_JOURNEY` at build time, so a static export cannot be re-pointed afterwards.
   `docker build --build-arg JOURNEY=<journey> ui-rm` produces it (`ui-ops` is the Angular shell and
   serves `ops`). Push it and record the digest. Terraform cannot read the journey back out of a
   digest: the `journey` field is the operator's claim, and `e2e/app_coverage.py` is what holds a
   hostname to the journey it actually renders.
2. **Mount at least one of that journey's apps** in `DEPLOY_EMBEDDED_APPS_JSON`, with its `-ui` and
   `-api` rollback digests. Without it the plan is refused, by the rule above.
3. **Derive the hostname.** With no managed zone, it comes from the load balancer's own address:
   `<journey>-<address with dots as dashes>.nip.io`. Read the address from the
   `load_balancer_address` output; never guess it, and never reuse one from a previous edge, because
   re-enabling the edge mints a different address and a certificate issued against a stale name
   never validates.
4. **Append** the entry to `DEPLOY_SHELLS_JSON` -- append, do not reorder (see the certificate note
   below) -- add its rollback digest under its journey key in `DEPLOY_ROLLBACK_IMAGES_JSON`, and add
   `https://<hostname>` to `DEPLOY_FRAME_ANCESTORS_JSON`. The tenant registry follows the shell list
   automatically; the IAP member list is already granted on every backend, so the new host admits
   exactly the members the existing ones do.
5. **Plan, read the plan, apply.** One apply. Expect creates for the service account, service, NEG,
   backend service, certificate and `random_id`, one IAP grant per approved member, in-place updates
   to the url map and the HTTPS proxy, and destroys of ONLY the old certificate and its `random_id`.

### What the certificate replacement costs

Changing the domain list replaces the managed certificate: the provider treats `managed.domains` as
ordered and ForceNew. That replacement is safe -- it is not the `resourceInUseByAnotherResource`
failure that broke the first domain rotation on 2026-08-29 -- because two things hold together:

- `random_id.certificate` keys on the joined domain list, so a changed list mints a **new
  certificate name** rather than trying to replace a name in place; and
- the certificate is `create_before_destroy`, so the new one exists, the HTTPS proxy is updated onto
  it in place, and only then is the old one deleted -- never while the proxy still references it.

This is the same mechanism the 2026-09-03 rotation onto a new load-balancer address executed in one
apply (2 creates / 2 destroys / 8 in-place changes, the destroys being only the old certificate and
its `random_id`). Adding a domain is that operation, not a different one.

What it does cost: **the replacement certificate starts in `PROVISIONING` for every domain,
including the hosts that were already live.** Google validates each domain by checking that it
resolves to the load balancer now serving the certificate, so the already-live hosts can present a
certificate a browser will not trust until validation completes -- minutes, on wildcard names that
already resolve. Plan the apply accordingly rather than during a demonstration, and check
`gcloud compute ssl-certificates describe <name>` for `ACTIVE` on every domain before declaring the
new host reachable. Because the order of `var.shells` is the order of that domain list, **appending
keeps the existing hosts' positions and reordering replaces the certificate for no gain.**

## External completion blockers

- target project, billing/quota and organization-policy authority;
- Artifact Registry images built, scanned, signed and available by digest;
- IAP OAuth/WIF registration and approved users/groups (the exact audience is emitted by stage
  one);
- DNS zone ownership and managed-certificate domain validation;
- Secret Manager entries and least-privilege data-service permissions for each embedded app,
  plus an exact BFF audit-HMAC secret version;
- reviewed notification channels, retention, backup/restore and incident ownership;
- controlled two-stage apply, unauthenticated denial, an authorized sign-in on every published
  persona host, proof that the
  BFF reaches each internal-only destination through Direct VPC egress, proof that its verifier
  retrieves current IAP signing keys through logged Cloud NAT, rollback and browser evidence.

`observability_url` is the exact `agent-observability` HTTPS origin; `observability_audience` is the independently
reviewed audience `agent-observability` verifies. The BFF runs the `platform` profile and acquires an
audience-bound workload token before posting each content-free access event. The adopter must
grant the portal service account Cloud Run invoke access and enroll it in `agent-observability`'s allowed-caller
policy. Per-hop OBO and tenant-specific issuer/audience variants remain deferred.
