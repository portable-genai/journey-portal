"""Deterministic hash-chain and durable local adapter tests."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from journey_portal.adapters.local.access_audit import LocalAccessAuditAdapter
from journey_portal.config import Settings
from journey_portal.domain.audit import GENESIS_HASH, PortalAuditService, audit_reference
from journey_portal.domain.models import PortalAccessEvent
from journey_portal.ports.access_audit import AuditUnavailable

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
        app_id="cdd-sow-research",
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


def _set_aside(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.set-aside-*"))


def test_local_adapter_persists_and_a_healthy_ledger_is_never_set_aside(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    adapter = LocalAccessAuditAdapter(settings)

    first = adapter.append(_event())
    second = adapter.append(_event("event-002"))
    reloaded = LocalAccessAuditAdapter(settings)

    assert reloaded.records() == (first, second)
    assert reloaded.pseudonym_key_id == adapter.pseudonym_key_id
    assert _set_aside(tmp_path) == []


def test_tampering_under_a_running_portal_sets_the_ledger_aside(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    adapter = LocalAccessAuditAdapter(Settings(local_audit_db=str(database)))
    adapter.append(_event())
    adapter.append(_event("event-002"))
    key_id = adapter.pseudonym_key_id
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE portal_access_audit SET action = ? WHERE sequence = 1",
            ("forward:ui",),
        )
        connection.commit()

    # Detection is intact: the view still says what happened before anything moves.
    assert adapter.integrity().valid is False

    record = adapter.append(_event("event-003"))

    assert record.sequence == 1
    assert record.previous_hash == GENESIS_HASH
    assert adapter.integrity().valid is True
    assert adapter.integrity().record_count == 1
    # The in-flight event was pseudonymised under the running key, so the fresh ledger keeps it.
    assert adapter.pseudonym_key_id == key_id
    kept = _set_aside(tmp_path)
    assert {p.name.split(".set-aside-")[0] for p in kept} >= {
        "audit.sqlite3",
        "audit.sqlite3.key",
        "audit.sqlite3.checkpoint",
    }
    # Nothing was deleted: the old trail, tampered row included, is still there to examine.
    old_db = next(p for p in kept if p.name.startswith("audit.sqlite3.set-aside-"))
    with closing(sqlite3.connect(old_db)) as connection:
        rows = connection.execute(
            "SELECT action FROM portal_access_audit ORDER BY sequence"
        ).fetchall()
    assert rows == [("forward:ui",), ("forward:api",)]


def test_tail_truncation_is_detected_then_set_aside(tmp_path: Path) -> None:
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
    assert adapter.append(_event("event-003")).sequence == 1
    assert adapter.integrity().valid is True
    assert _set_aside(tmp_path)


def test_a_ledger_rolled_back_behind_its_checkpoint_starts_fresh(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    adapter = LocalAccessAuditAdapter(settings)
    adapter.append(_event())
    snapshot = tmp_path / "older-copy.sqlite3"
    shutil.copy(database, snapshot)
    adapter.append(_event("event-002"))
    adapter.append(_event("event-003"))
    del adapter
    shutil.copy(snapshot, database)

    with caplog.at_level(logging.WARNING, logger="hex_service_kit.audit"):
        restarted = LocalAccessAuditAdapter(settings)

    assert "set aside" in caplog.text
    assert "failed integrity verification" in caplog.text
    assert restarted.records() == ()
    assert restarted.integrity().valid is True
    assert restarted.append(_event("event-004")).sequence == 1
    assert len(_set_aside(tmp_path)) >= 3


def test_an_incomplete_set_is_set_aside_with_a_new_key(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    first_key_id = LocalAccessAuditAdapter(settings).pseudonym_key_id
    database.unlink()
    Path(f"{database}.checkpoint").unlink()

    restarted = LocalAccessAuditAdapter(settings)

    assert restarted.pseudonym_key_id != first_key_id
    assert restarted.integrity().valid is True
    assert [p.name.split(".set-aside-")[0] for p in _set_aside(tmp_path)] == ["audit.sqlite3.key"]


def test_a_forged_checkpoint_is_set_aside(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    LocalAccessAuditAdapter(settings).append(_event())
    Path(f"{database}.checkpoint").write_text('{"record_count": 0, "head_hash": "x"}\n')

    restarted = LocalAccessAuditAdapter(settings)

    assert restarted.integrity().valid is True
    assert restarted.records() == ()
    assert _set_aside(tmp_path)


def test_a_corrupt_database_file_is_set_aside(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    LocalAccessAuditAdapter(settings).append(_event())
    database.write_bytes(b"not a sqlite database, fictional corruption" * 100)
    for sidecar in (Path(f"{database}-wal"), Path(f"{database}-shm")):
        sidecar.unlink(missing_ok=True)

    restarted = LocalAccessAuditAdapter(settings)

    assert restarted.append(_event("event-002")).sequence == 1
    assert restarted.integrity().valid is True
    assert _set_aside(tmp_path)


def test_a_truncated_key_is_set_aside(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    settings = Settings(local_audit_db=str(database))
    LocalAccessAuditAdapter(settings).append(_event())
    Path(f"{database}.key").write_bytes(b"short")

    restarted = LocalAccessAuditAdapter(settings)

    assert len(Path(f"{database}.key").read_bytes()) == 32
    assert restarted.integrity().valid is True
    assert _set_aside(tmp_path)


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
