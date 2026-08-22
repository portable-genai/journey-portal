"""Deterministic hash-chain construction and verification for portal access evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from .models import (
    AuditIntegrityFinding,
    AuditSeverity,
    PortalAccessEvent,
    PortalAccessRecord,
    PortalAuditView,
)

GENESIS_HASH = "0" * 64


def audit_reference(key: bytes, kind: str, value: str) -> str:
    """Create a deployment-scoped, domain-separated pseudonymous identity reference."""
    if len(key) < 32:
        raise ValueError("audit HMAC key must contain at least 32 bytes")
    material = f"{kind}\0{value}".encode()
    return hmac.new(key, material, hashlib.sha256).hexdigest()


def audit_key_id(key: bytes) -> str:
    """Return a non-secret identifier used to distinguish pseudonym-key rotations."""
    if len(key) < 32:
        raise ValueError("audit HMAC key must contain at least 32 bytes")
    return hashlib.sha256(key).hexdigest()[:16]


def _record_digest(sequence: int, event: PortalAccessEvent, previous_hash: str) -> str:
    payload = {
        "action": event.action,
        "actor_ref": event.actor_ref,
        "app_id": event.app_id,
        "event_id": event.event_id,
        "method": event.method,
        "occurred_at": event.occurred_at,
        "pseudonym_key_id": event.pseudonym_key_id,
        "previous_hash": previous_hash,
        "sequence": sequence,
        "tenant_ref": event.tenant_ref,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PortalAuditService:
    """Pure, deterministic builder and verifier for the access-event hash chain."""

    def build_record(
        self,
        *,
        sequence: int,
        event: PortalAccessEvent,
        previous_hash: str,
    ) -> PortalAccessRecord:
        if sequence < 1:
            raise ValueError("audit sequence must be positive")
        if len(previous_hash) != 64:
            raise ValueError("previous audit hash must be a SHA-256 hex digest")
        return PortalAccessRecord(
            sequence=sequence,
            event=event,
            previous_hash=previous_hash,
            record_hash=_record_digest(sequence, event, previous_hash),
        )

    def verify(
        self,
        records: tuple[PortalAccessRecord, ...],
        *,
        expected_count: int | None = None,
        expected_head_hash: str | None = None,
    ) -> PortalAuditView:
        findings: list[AuditIntegrityFinding] = []
        expected_previous = GENESIS_HASH

        for expected_sequence, record in enumerate(records, start=1):
            evidence_id = f"audit-sequence:{record.sequence}"
            if record.sequence != expected_sequence:
                findings.append(
                    AuditIntegrityFinding(
                        finding_id=f"sequence:{expected_sequence}",
                        severity=AuditSeverity.HIGH,
                        summary="Audit sequence is not contiguous",
                        detail=(f"Expected sequence {expected_sequence}, found {record.sequence}."),
                        evidence_id=evidence_id,
                    )
                )
            if record.previous_hash != expected_previous:
                findings.append(
                    AuditIntegrityFinding(
                        finding_id=f"previous-hash:{record.sequence}",
                        severity=AuditSeverity.HIGH,
                        summary="Previous audit hash does not match",
                        detail=f"Sequence {record.sequence} is detached from the preceding record.",
                        evidence_id=evidence_id,
                    )
                )
            expected_hash = _record_digest(
                record.sequence,
                record.event,
                record.previous_hash,
            )
            if record.record_hash != expected_hash:
                findings.append(
                    AuditIntegrityFinding(
                        finding_id=f"record-hash:{record.sequence}",
                        severity=AuditSeverity.HIGH,
                        summary="Audit record digest does not match",
                        detail=f"Sequence {record.sequence} was modified after it was recorded.",
                        evidence_id=evidence_id,
                    )
                )
            expected_previous = record.record_hash

        if expected_count is not None and len(records) != expected_count:
            findings.append(
                AuditIntegrityFinding(
                    finding_id="checkpoint-count",
                    severity=AuditSeverity.HIGH,
                    summary="Audit record count does not match the retained checkpoint",
                    detail=f"Expected {expected_count} records, found {len(records)}.",
                    evidence_id="audit-checkpoint",
                )
            )
        actual_head = records[-1].record_hash if records else GENESIS_HASH
        if expected_head_hash is not None and actual_head != expected_head_hash:
            findings.append(
                AuditIntegrityFinding(
                    finding_id="checkpoint-head",
                    severity=AuditSeverity.HIGH,
                    summary="Audit head hash does not match the retained checkpoint",
                    detail="The retained ledger may have been truncated or replaced.",
                    evidence_id="audit-checkpoint",
                )
            )

        ordered = tuple(sorted(findings, key=lambda item: item.finding_id))
        return PortalAuditView(
            valid=not ordered,
            record_count=len(records),
            head_hash=actual_head,
            findings=ordered,
            suggested_actions=(
                (
                    "Stop relying on the local ledger and preserve the database for investigation.",
                    "Compare the last retained head hash with the current chain.",
                    "Restore from a reviewed backup before resuming the portal.",
                )
                if ordered
                else ()
            ),
        )
