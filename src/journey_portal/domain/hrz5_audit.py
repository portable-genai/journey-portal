"""Deterministic mapping from portal access evidence to the Hrz5 wire contract."""

from __future__ import annotations

from .models import PortalAccessEvent


def to_hrz5_audit_event(event: PortalAccessEvent) -> dict[str, object]:
    """Map one content-free portal event to Hrz5 without introducing user content."""
    decision = (
        "blocked"
        if event.action in {"embed-policy:denied", "embed-policy:denied-preflight"}
        else "allowed"
    )
    resource = (
        "hrz9-journey-portal/embed-policy"
        if event.app_id.startswith("portal:")
        else f"hrz9-journey-portal/{event.app_id}"
    )
    return {
        "action": event.action,
        "actor": event.actor_ref,
        "decision": decision,
        "redacted_prompt": "",
        "redacted_response": "",
        "citations": [],
        "resource": resource,
        "trace_id": None,
        "timestamp": event.occurred_at,
        "metadata": {
            "event_id": event.event_id,
            "method": event.method,
            "pseudonym_key_id": event.pseudonym_key_id,
            "source": "hrz9-journey-portal",
            "tenant_ref": event.tenant_ref,
        },
    }
