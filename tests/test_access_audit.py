"""Deterministic hash-chain and durable local adapter tests."""

from __future__ import annotations

import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from journey_portal.adapters.local.access_audit import LocalAccessAuditAdapter
from journey_portal.config import Settings
from journey_portal.domain.audit import GENESIS_HASH, PortalAuditService, audit_reference
from journey_portal.domain.models import PortalAccessEvent
from journey_portal.ports.access_audit import AuditIntegrityViolation, AuditUnavailable

_KEY = b"fictional-local-audit-key-32-bytes"


def _event(event_id: str = "event-001") -> PortalAccessEvent:
    return PortalAccessEvent(
        event_id=event_id,
        occurred_at="2026-07-29T12:00:00+00:00",
        actor_ref=audit_reference(_KEY, "actor", "fictional.user@example.test"),
        tenant_ref=audit_reference(_KEY, "tenant", "fictional-bank"),
        pseudonym_key_id="fictional-key-id",
        method="POST",
        action="forward:api",
        app_id="doc1",
    )


def test_hash_chain_is_deterministic_and_valid() -> None:
    service = PortalAuditService()
    first = service.build_record(sequence=1, event=_event(), previous_hash=GENESIS_HASH)
    repeated = service.build_record(sequence=1, event=_event(), previous_hash=GENESIS_HASH)
    second = service.build_record(
        sequence=2,
        event=_event("event-002"),
        previous_hash=first.record_hash,
    )

    assert first == repeated
    view = service.verify((first, second))
    assert view.valid is True
    assert view.escalates is False
    assert view.record_count == 2
    assert view.head_hash == second.record_hash


def test_modified_event_escalates_with_evidence() -> None:
    service = PortalAuditService()
    first = service.build_record(sequence=1, event=_event(), previous_hash=GENESIS_HASH)
    changed = replace(first, event=replace(first.event, action="forward:ui"))

    view = service.verify((changed,))

    assert view.valid is False
    assert view.escalates is True
    assert [finding.finding_id for finding in view.findings] == ["record-hash:1"]
    assert view.findings[0].evidence_id == "audit-sequence:1"
    assert view.suggested_actions


def test_gap_and_detached_record_are_severity_ranked() -> None:
    service = PortalAuditService()
    record = service.build_record(sequence=2, event=_event(), previous_hash="f" * 64)

    view = service.verify((record,))

    assert [finding.finding_id for finding in view.findings] == [
        "previous-hash:2",
        "sequence:1",
    ]
    assert {finding.severity.value for finding in view.findings} == {"high"}


def test_local_adapter_persists_and_rejects_tampering(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    adapter = LocalAccessAuditAdapter(settings)

    first = adapter.append(_event())
    second = adapter.append(_event("event-002"))
    reloaded = LocalAccessAuditAdapter(settings)

    assert reloaded.records() == (first, second)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE portal_access_audit SET action = ? WHERE sequence = 1",
            ("forward:ui",),
        )

    with pytest.raises(RuntimeError, match="failed integrity"):
        reloaded.append(_event("event-003"))


def test_tail_truncation_is_detected_against_checkpoint(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    adapter = LocalAccessAuditAdapter(Settings(local_audit_db=str(database)))
    adapter.append(_event())
    adapter.append(_event("event-002"))
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM portal_access_audit WHERE sequence = 2")

    view = adapter.integrity()

    assert view.valid is False
    assert {finding.finding_id for finding in view.findings} == {
        "checkpoint-count",
        "checkpoint-head",
    }
    with pytest.raises(AuditIntegrityViolation, match="failed integrity"):
        adapter.append(_event("event-003"))


def test_local_audit_files_are_owner_only(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    adapter = LocalAccessAuditAdapter(Settings(local_audit_db=str(database)))
    adapter.append(_event())

    for path in (
        database,
        Path(f"{database}.key"),
        Path(f"{database}.checkpoint"),
        Path(f"{database}.checkpoint.lock"),
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_checkpoint_recovers_if_commit_finishes_before_final_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    adapter = LocalAccessAuditAdapter(settings)

    def fail_final_publish(_record_count: int, _head_hash: str) -> None:
        raise OSError("fictional crash after database commit")

    monkeypatch.setattr(adapter, "_write_committed_checkpoint", fail_final_publish)

    with pytest.raises(AuditUnavailable, match="database is unavailable"):
        adapter.append(_event())

    recovered = LocalAccessAuditAdapter(settings)
    assert recovered.integrity().valid is True
    assert recovered.integrity().record_count == 1


def test_existing_key_prevents_silent_ledger_reset(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    LocalAccessAuditAdapter(settings)
    database.unlink()
    Path(f"{database}.checkpoint").unlink()

    with pytest.raises(AuditIntegrityViolation, match="state is incomplete"):
        LocalAccessAuditAdapter(settings)


def test_concurrent_append_and_integrity_use_one_coherent_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    adapters = (LocalAccessAuditAdapter(settings), LocalAccessAuditAdapter(settings))

    def append_one(index: int) -> bool:
        adapter = adapters[index % len(adapters)]
        adapter.append(_event(f"event-{index:03d}"))
        return adapter.integrity().valid

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(append_one, range(1, 21)))

    final = adapters[0].integrity()
    assert all(results)
    assert final.valid is True
    assert final.record_count == 20


def test_concurrent_first_initializers_never_observe_partial_key(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))

    with ThreadPoolExecutor(max_workers=2) as executor:
        key_ids = tuple(
            executor.map(
                lambda _index: LocalAccessAuditAdapter(settings).pseudonym_key_id,
                range(2),
            )
        )

    assert key_ids[0] == key_ids[1]
    assert len(Path(f"{database}.key").read_bytes()) == 32


def test_constructor_filesystem_failure_is_translated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_parent(_parent: Path) -> None:
        raise OSError("fictional read-only filesystem")

    monkeypatch.setattr(
        LocalAccessAuditAdapter,
        "_prepare_parent",
        staticmethod(fail_parent),
    )

    with pytest.raises(AuditUnavailable, match="database is unavailable"):
        LocalAccessAuditAdapter(Settings(local_audit_db=str(tmp_path / "audit.sqlite3")))


def test_audit_references_are_keyed_and_domain_separated() -> None:
    other_key = b"another-fictional-audit-key-32byt"
    assert audit_reference(_KEY, "actor", "same") == audit_reference(_KEY, "actor", "same")
    assert audit_reference(_KEY, "actor", "same") != audit_reference(_KEY, "tenant", "same")
    assert audit_reference(_KEY, "actor", "same") != audit_reference(other_key, "actor", "same")
