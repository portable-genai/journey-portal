# Compliance and audit

`journey-portal` retains content-free load-balancer, IAP and Cloud Run access evidence in a regional Logging
bucket. Retention defaults to 30 days, the window `_Default` keeps anyway; an adopter raises it and approves the irreversible lock in its own deployment file. The four posture alerts (IAP denials, service-account keys, VPC-SC denials, CMEK changes) exist only where `posture_alerts_enabled` is true. Embedded application decisions
remain in their own audit records, with `agent-observability` as the catalog observability destination and `model-quality-gate`
as promotion authority.
