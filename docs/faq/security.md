# Security and identity

IAP authenticates the public load-balancer edge. `journey-portal` verifies the assertion, discards browser
identity claims, and authenticates private Cloud Run calls with workload identity. Embedded
applications still enforce their own object and tenant authorization. Per-hop OBO remains a
deferred `journey-portal` hardening item, and `compliance-advisory`, `onprem-dlp` own broader control and adversarial assurance.
