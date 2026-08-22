"""SQLite local access ledger with keyed pseudonyms and retained checkpoints."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from fcntl import LOCK_EX, LOCK_UN, flock
from pathlib import Path

from ...config import Settings
from ...domain.audit import (
    GENESIS_HASH,
    PortalAuditService,
    audit_key_id,
    audit_reference,
)
from ...domain.models import PortalAccessEvent, PortalAccessRecord, PortalAuditView
from ...ports.access_audit import AuditIntegrityViolation, AuditUnavailable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_access_audit (
    sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    tenant_ref TEXT NOT NULL,
    pseudonym_key_id TEXT NOT NULL,
    method TEXT NOT NULL,
    action TEXT NOT NULL,
    app_id TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class _Checkpoint:
    phase: str
    record_count: int
    head_hash: str
    pending_record_count: int | None = None
    pending_head_hash: str | None = None


class LocalAccessAuditAdapter:
    """Append atomically and fail closed on a damaged or rolled-back local ledger."""

    def __init__(self, settings: Settings) -> None:
        self._path = Path(settings.local_audit_db)
        self._key_path = Path(settings.local_audit_key_file or f"{settings.local_audit_db}.key")
        self._checkpoint_path = Path(
            settings.local_audit_checkpoint or f"{settings.local_audit_db}.checkpoint"
        )
        self._lock_path = self._checkpoint_path.with_name(f"{self._checkpoint_path.name}.lock")
        self._service = PortalAuditService()
        try:
            self._prepare_parent(self._path.parent)
            self._prepare_parent(self._key_path.parent)
            self._prepare_parent(self._checkpoint_path.parent)
            with self._locked():
                present = (
                    self._path.exists(),
                    self._key_path.exists(),
                    self._checkpoint_path.exists(),
                )
                if any(present) and not all(present):
                    raise AuditIntegrityViolation(
                        "local access audit state is incomplete; restore the database, key, "
                        "and retained checkpoint together"
                    )
                if not any(present):
                    self._create_key()
                    self._precreate_private(self._path)
                self._key = self._load_key()
                self._key_id = audit_key_id(self._key)
                with self._connect() as connection:
                    connection.execute(_SCHEMA)
                    if not any(present):
                        self._write_committed_checkpoint(0, GENESIS_HASH)
                    else:
                        self._coherent_snapshot(connection)
        except (AuditIntegrityViolation, AuditUnavailable):
            raise
        except (OSError, sqlite3.Error) as exc:
            raise AuditUnavailable("local access audit database is unavailable") from exc

    @staticmethod
    def _prepare_parent(parent: Path) -> None:
        existed = parent.exists()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not existed or parent.name == ".local":
            parent.chmod(0o700)
        mode = stat.S_IMODE(parent.stat().st_mode)
        if mode & 0o077:
            raise AuditUnavailable(
                f"access audit directory must not be group/world accessible: {parent}"
            )

    @staticmethod
    def _precreate_private(path: Path) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        path.chmod(0o600)

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @classmethod
    def _write_private(cls, path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            path.chmod(0o600)
            cls._fsync_parent(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _create_key(self) -> None:
        temporary = self._key_path.with_name(f".{self._key_path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
            with suppress(FileExistsError):
                os.link(temporary, self._key_path)
            self._key_path.chmod(0o600)
            self._fsync_parent(self._key_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load_key(self) -> bytes:
        self._key_path.chmod(0o600)
        key = self._key_path.read_bytes()
        if len(key) < 32:
            raise AuditUnavailable("local access audit HMAC key is invalid")
        return key

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            flock(descriptor, LOCK_EX)
            yield
        finally:
            flock(descriptor, LOCK_UN)
            os.close(descriptor)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        for sidecar in (
            self._path,
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        ):
            if sidecar.exists():
                sidecar.chmod(0o600)
        return connection

    @staticmethod
    def _records(connection: sqlite3.Connection) -> tuple[PortalAccessRecord, ...]:
        rows = connection.execute(
            """
            SELECT sequence, event_id, occurred_at, actor_ref, tenant_ref, pseudonym_key_id,
                   method, action, app_id, previous_hash, record_hash
              FROM portal_access_audit
             ORDER BY sequence
            """
        ).fetchall()
        return tuple(
            PortalAccessRecord(
                sequence=int(row[0]),
                event=PortalAccessEvent(
                    event_id=str(row[1]),
                    occurred_at=str(row[2]),
                    actor_ref=str(row[3]),
                    tenant_ref=str(row[4]),
                    pseudonym_key_id=str(row[5]),
                    method=str(row[6]),
                    action=str(row[7]),
                    app_id=str(row[8]),
                ),
                previous_hash=str(row[9]),
                record_hash=str(row[10]),
            )
            for row in rows
        )

    def _legacy_checkpoint_signature(self, record_count: int, head_hash: str) -> str:
        payload = f"{record_count}:{head_hash}:{self._key_id}".encode()
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def _checkpoint_signature(self, document: dict[str, object]) -> str:
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def _write_checkpoint(self, checkpoint: _Checkpoint) -> None:
        document: dict[str, object] = {
            "format_version": 2,
            "phase": checkpoint.phase,
            "head_hash": checkpoint.head_hash,
            "pseudonym_key_id": self._key_id,
            "record_count": checkpoint.record_count,
        }
        if checkpoint.phase == "pending":
            document["pending_head_hash"] = checkpoint.pending_head_hash
            document["pending_record_count"] = checkpoint.pending_record_count
        document["signature"] = self._checkpoint_signature(document)
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        self._write_private(self._checkpoint_path, encoded + b"\n")

    def _write_committed_checkpoint(self, record_count: int, head_hash: str) -> None:
        self._write_checkpoint(_Checkpoint("committed", record_count, head_hash))

    def _write_pending_checkpoint(
        self,
        record_count: int,
        head_hash: str,
        pending_record_count: int,
        pending_head_hash: str,
    ) -> None:
        self._write_checkpoint(
            _Checkpoint(
                "pending",
                record_count,
                head_hash,
                pending_record_count,
                pending_head_hash,
            )
        )

    def _read_checkpoint(self) -> _Checkpoint:
        try:
            document = json.loads(self._checkpoint_path.read_text())
            record_count = int(document["record_count"])
            head_hash = str(document["head_hash"])
            key_id = str(document["pseudonym_key_id"])
            signature = str(document["signature"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise AuditIntegrityViolation("local access audit checkpoint is invalid") from exc
        if document.get("format_version") is None:
            expected = self._legacy_checkpoint_signature(record_count, head_hash)
            checkpoint = _Checkpoint("committed", record_count, head_hash)
        else:
            try:
                phase = str(document["phase"])
                unsigned = {key: value for key, value in document.items() if key != "signature"}
                expected = self._checkpoint_signature(unsigned)
                pending_count = (
                    int(document["pending_record_count"]) if phase == "pending" else None
                )
                pending_head = str(document["pending_head_hash"]) if phase == "pending" else None
                checkpoint = _Checkpoint(
                    phase,
                    record_count,
                    head_hash,
                    pending_count,
                    pending_head,
                )
            except (ValueError, TypeError, KeyError) as exc:
                raise AuditIntegrityViolation("local access audit checkpoint is invalid") from exc
        invalid_pending = checkpoint.phase == "pending" and (
            checkpoint.pending_record_count != record_count + 1
            or checkpoint.pending_head_hash is None
            or len(checkpoint.pending_head_hash) != 64
        )
        if (
            checkpoint.phase not in {"committed", "pending"}
            or record_count < 0
            or len(head_hash) != 64
            or key_id != self._key_id
            or invalid_pending
            or not hmac.compare_digest(signature, expected)
        ):
            raise AuditIntegrityViolation("local access audit checkpoint signature is invalid")
        self._checkpoint_path.chmod(0o600)
        return checkpoint

    def _coherent_snapshot(
        self, connection: sqlite3.Connection
    ) -> tuple[tuple[PortalAccessRecord, ...], int, str]:
        checkpoint = self._read_checkpoint()
        records = self._records(connection)
        view = self._service.verify(records)
        actual_count = len(records)
        actual_head = records[-1].record_hash if records else GENESIS_HASH
        if checkpoint.phase == "pending" and view.valid:
            candidates = {
                (checkpoint.record_count, checkpoint.head_hash),
                (checkpoint.pending_record_count, checkpoint.pending_head_hash),
            }
            if (actual_count, actual_head) in candidates:
                self._write_committed_checkpoint(actual_count, actual_head)
                return records, actual_count, actual_head
        return records, checkpoint.record_count, checkpoint.head_hash

    @property
    def pseudonym_key_id(self) -> str:
        return self._key_id

    def reference(self, kind: str, value: str) -> str:
        return audit_reference(self._key, kind, value)

    def append(self, event: PortalAccessEvent) -> PortalAccessRecord:
        try:
            with self._locked(), self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current, expected_count, expected_head = self._coherent_snapshot(connection)
                assessment = self._service.verify(
                    current,
                    expected_count=expected_count,
                    expected_head_hash=expected_head,
                )
                if not assessment.valid:
                    raise AuditIntegrityViolation(
                        "local access audit chain failed integrity verification"
                    )
                previous_hash = current[-1].record_hash if current else GENESIS_HASH
                record = self._service.build_record(
                    sequence=len(current) + 1,
                    event=event,
                    previous_hash=previous_hash,
                )
                connection.execute(
                    """
                    INSERT INTO portal_access_audit (
                        sequence, event_id, occurred_at, actor_ref, tenant_ref,
                        pseudonym_key_id, method, action, app_id, previous_hash, record_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.sequence,
                        event.event_id,
                        event.occurred_at,
                        event.actor_ref,
                        event.tenant_ref,
                        event.pseudonym_key_id,
                        event.method,
                        event.action,
                        event.app_id,
                        record.previous_hash,
                        record.record_hash,
                    ),
                )
                self._write_pending_checkpoint(
                    len(current),
                    previous_hash,
                    record.sequence,
                    record.record_hash,
                )
                connection.commit()
                self._write_committed_checkpoint(record.sequence, record.record_hash)
                return record
        except AuditUnavailable:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise AuditUnavailable("local access audit database is unavailable") from exc

    def records(self) -> tuple[PortalAccessRecord, ...]:
        try:
            with self._locked(), self._connect() as connection:
                return self._records(connection)
        except AuditUnavailable:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise AuditUnavailable("local access audit database is unavailable") from exc

    def integrity(self) -> PortalAuditView:
        try:
            with self._locked(), self._connect() as connection:
                records, expected_count, expected_head = self._coherent_snapshot(connection)
                return self._service.verify(
                    records,
                    expected_count=expected_count,
                    expected_head_hash=expected_head,
                )
        except AuditUnavailable:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise AuditUnavailable("local access audit database is unavailable") from exc
