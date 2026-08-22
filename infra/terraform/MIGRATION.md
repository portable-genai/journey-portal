# Existing BFF migration

The original stack managed only `google_cloud_run_v2_service.portal` and
`google_service_account.portal`. Their Terraform addresses are unchanged. Do not remove them from
state or import a second copy.

Before the first plan:

1. Back up the encrypted remote state and record the deployed BFF revision.
2. Set `name_prefix = "hrz-journey"` if the existing resource names are
   `journey-portal`. A different prefix forces replacement.
3. Replace tag-based image inputs with the exact deployed digest.
4. Add IAP, DNS, shell, and embedded-app inputs, keeping `apply_org_policies = false` and
   `lock_audit_bucket = false`.
5. Run `terraform plan -out=migration.tfplan`. Refuse any unexpected BFF or service-account
   replacement.
6. Apply the additive edge and service resources. Enable organization policies only in a later,
   separately approved plan. Lock audit retention only after restore and legal review.

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
