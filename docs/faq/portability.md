# Portability

The executable proof covers two host frameworks, the shared journey contract, identity-header
isolation, complete adapter bindings and a fail-fast on-prem seam. It does not prove model or data
portability because `journey-portal` owns neither. It also does not claim a working on-prem adapter or
tenant-specific issuer registry. Host-bound tenant framing/CORS policy is implemented and remains
pure stdlib; a real on-prem edge must supply and retain the reviewed policy document. See
`scripts/portability_demo.py` and `docs/onprem-migration.md`.
