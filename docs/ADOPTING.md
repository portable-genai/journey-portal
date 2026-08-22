# Adopting Hrz9

Hrz9 is a host contract, not a bank-specific portal. Keep the domain routing and identity
boundary upstream-compatible, then own the journey composition, institution edge, and visual
shells in the adopter repository.

## Boundary

| Upstream-maintained | Adopter-owned |
|---|---|
| `src/journey_portal/domain/`, tenant-policy engine, ports, adapter contracts, proxy sanitization, tests and evals | `config/journeys.yaml`, RM/Ops shell presentation, approved host-to-tenant policies, IAP registration and Terraform input values |
| Container and Terraform module structure | Image digests, region allowlist, sizing, notification channels, secret names and retention approvals |
| Local content-free access ledger, integrity view, demo self-test and bounded portability proof | Institution evidence-sink retention, target-host browser evidence and production evidence retention |

The domain has two stable parts: `models.py` carries portable value objects, while
`catalog.py` and `identity_injection.py` carry Hrz9's journey and trust-boundary behavior. A fork
may add fields without weakening header stripping, secure upstream validation, or server-side
identity resolution.

## Mechanised rename

Run this only in a clean scratch clone. The default is a dry run:

```bash
python scripts/rename_fork.py \
  --package bank_journey_portal \
  --project bank-journey-portal \
  --cli bank-journey \
  --env-prefix BANK_JOURNEY \
  --resource-prefix bank-journey
```

Review the file count and package move, rerun with `--apply`, then run `make check`,
`make demo-selftest`, `make portability`, both shell builds, and `make tf-validate`.

## Decisions the adopter must make

- approved region and region allowlist;
- IAP and corporate IdP ownership, exact IAP audience, authorized groups and break-glass path;
- stable tenant id, RM/Ops hostnames, DNS/TLS ownership, frame ancestors and any exceptional CORS
  origins; each routed hostname must belong to exactly one tenant policy;
- reviewed embedded application image digests, secret names, service sizing and quotas;
- Org Policy authority, audit retention and whether the retention lock is approved;
- the retained head-hash checkpoint location for local-ledger investigations;
- alert notification channels, incident ownership, rollout, rollback and evidence retention;
- fictional demo fixtures and the institution-owned browser/e2e golden journey.

Keep OBO and tenant-specific issuer/audience variants as explicit decisions. Host-bound
per-tenant framing/CORS policy and content-free Hrz5 access-log delivery are implemented. Supply
the exact Hrz5 HTTPS origin and its verified token audience, then grant the portal service account
Hrz5 invoke/caller access; do not replace the tenant policy with a union allowlist.
