# Security and identity

IAP authenticates the public load-balancer edge. Hrz9 verifies the assertion, discards browser
identity claims, and authenticates private Cloud Run calls with workload identity. Embedded
applications still enforce their own object and tenant authorization. Per-hop OBO remains a
deferred Hrz9 hardening item, and Rsk1/Rsk6 own broader control and adversarial assurance.
