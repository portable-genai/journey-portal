# Features and boundaries

`journey-portal` composes configured application UIs and APIs into RM and Ops journeys. It owns the host,
route and identity boundary. `agent-guardrail-gateway` owns guardrails, `enterprise-knowledge-base` grounding, `agent-registry`, `model-quality-gate` promotion,
`agent-observability`, and `human-review-console` human review. `journey-portal` links to those systems instead of duplicating
their engines.
