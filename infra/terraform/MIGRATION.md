# Existing BFF migration

The original stack managed only `google_cloud_run_v2_service.portal` and
`google_service_account.portal`. Their Terraform addresses are unchanged. Do not remove them from
state or import a second copy.

Before the first plan:

1. Back up the encrypted remote state and record the deployed BFF revision.
2. Set `name_prefix = "hrz-journey"` if the existing resource names are
   `journey-portal`. A different prefix forces replacement.
3. Replace tag-based image inputs with the exact deployed digest.
4. Add IAP, DNS, shell (`DEPLOY_SHELLS_JSON`, one `{journey, image, domain}` entry per persona
   host, in the order the existing managed certificate lists the domains) and embedded-app inputs,
   keeping `apply_org_policies = false` and `lock_audit_bucket = false`.
5. Run `terraform plan -out=migration.tfplan`. Refuse any unexpected BFF or service-account
   replacement.
6. Apply the additive edge and service resources. Enable organization policies only in a later,
   separately approved plan. Lock audit retention only after restore and legal review.

## An existing two-shell state

A deployment applied before 2026-09-12 holds its two shells at the single addresses
`google_service_account.rm_shell`, `google_cloud_run_v2_service.rm_shell` and their `ops_shell`
twins, plus the matching NEG, backend service and IAP invoker. They now live in one `for_each` map
keyed by journey. `shells.tf` carries a `moved` block for each, so **Terraform relocates them in
state and a plan for a deployment naming the same two shells in the same order shows no shell
create or destroy.** Nothing is imported and nothing is removed from state by hand.

Two in-place changes are expected on that first plan and are not drift:

- the RM shell's startup and liveness probes move from `/` to `/healthz`, the path the static
  server both shell images ship already answers, because one `for_each` resource cannot probe two
  paths. That is a new revision for the RM service only; Ops already probed `/healthz`.
- the managed certificate is replaced only if the domain LIST changed. Keeping the same hostnames
  in the same order keeps the same `random_id` keeper and therefore the same certificate name, so
  the edge is untouched. Read [README.md](README.md) ("Adding a shell host") before changing it.

If the old resources were created outside this state, import them at the unchanged addresses
before planning:

```bash
terraform import google_service_account.portal \
  projects/PROJECT/serviceAccounts/journey-portal@PROJECT.iam.gserviceaccount.com
terraform import google_cloud_run_v2_service.portal \
  projects/PROJECT/locations/REGION/services/journey-portal
```

Capture the pre-migration state serial, reviewed plan, apply output, service revisions, health
checks, and rollback decision in the deployment evidence pack.
