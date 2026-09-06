# Compliance and audit

`journey-portal` retains content-free load-balancer, IAP and Cloud Run access evidence in a regional Logging
bucket. Retention defaults to 180 days; an adopter approves any change and the irreversible lock. Embedded application decisions
remain in their own audit records, with `agent-observability` as the catalog observability destination and `model-quality-gate`
as promotion authority.
