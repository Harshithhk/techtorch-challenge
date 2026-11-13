"""Writable sandbox: the only place the app writes. Files live in data/sandbox/.

Layout:
- plans.json, invoices.json, credit_memos.json: sandbox records, same shape as the source rows.
- applied_actions.json: registry of applied actions, so rollback can find the record to remove.
- audit_log.json: append-only event list (applied, rolled_back, rejected).

Every change runs under an exclusive lock (threading lock plus fcntl file lock) and each file is
replaced atomically (write temp file, then os.replace). A crash between the record write and
the registry write leaves a record without a registry entry. Startup does not repair this;
it is documented as a known limit.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.storage.errors import SandboxConflictError

_THREAD_LOCK = threading.Lock()

# table name -> (file name, primary key field)
TABLES: dict[str, tuple[str, str]] = {
    "plans": ("plans.json", "plan_id"),
    "invoices": ("invoices.json", "invoice_id"),
    "credit_memos": ("credit_memos.json", "memo_id"),
}


class SandboxStore:
    """Read and write the sandbox directory. All mutations go through the methods below."""

    def __init__(self, directory: Path, clock: Callable[[], datetime] | None = None) -> None:
        self._dir = Path(directory)
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def directory(self) -> Path:
        return self._dir

    # ---- low-level file helpers -------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the process lock and the sandbox file lock for the duration of one change."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with _THREAD_LOCK, open(self._dir / ".lock", "a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self._dir / name
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SandboxConflictError(f"sandbox file {name} is not a JSON array")
        return data

    def _write(self, name: str, rows: list[dict[str, Any]]) -> None:
        """Atomic replace: write a temp file in the same directory, then rename over the target."""
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / name
        tmp = target.with_name(f".{name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, target)

    # ---- reads ----------------------------------------------------------------

    def read_records(self, table: str) -> list[dict[str, Any]]:
        """Sandbox records for one table. Output: list of dicts in write order."""
        return self._read(TABLES[table][0])

    def read_applied(self) -> list[dict[str, Any]]:
        """Registry of applied actions, oldest first."""
        return self._read("applied_actions.json")

    def read_audit(self) -> list[dict[str, Any]]:
        """Full audit trail, oldest first. This is read-only: there is no update or delete."""
        return self._read("audit_log.json")

    # ---- mutations --------------------------------------------------------------

    def commit_action(
        self,
        *,
        action_id: str,
        kind: str,
        table: str,
        record: dict[str, Any],
        plan_id: str,
        summary: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Apply an approved action: add the record, register it, and append an 'applied' audit event.

        Input: action id and kind, target table, the record to add (JSON-ready), plan id, summary, user id.
        Output: the registry entry written.
        Raises SandboxConflictError if the record's primary key already exists in the table.
        """
        filename, key = TABLES[table]
        key_value = record[key]
        with self._locked():
            rows = self._read(filename)
            if any(row.get(key) == key_value for row in rows):
                raise SandboxConflictError(f"{table} already has {key}={key_value}")
            rows.append(record)
            self._write(filename, rows)

            entry = {
                "action_id": action_id,
                "kind": kind,
                "table": table,
                "key": key,
                "key_value": key_value,
                "plan_id": plan_id,
                "summary": summary,
                "applied_at": self._clock().isoformat(),
            }
            registry = self._read("applied_actions.json")
            registry.append(entry)
            self._write("applied_actions.json", registry)
            self._append_audit_locked({"event": "applied", "action_id": action_id, "kind": kind,
                                       "plan_id": plan_id, "summary": summary, "user_id": user_id})
        return entry

    def revert_action(self, *, action_id: str, user_id: str) -> dict[str, Any]:
        """Roll back an applied action: remove its record, drop the registry entry, log 'rolled_back'.

        Input: action id, user id for the audit trail.
        Output: the removed registry entry.
        Raises SandboxConflictError if the action id is unknown.
        """
        with self._locked():
            registry = self._read("applied_actions.json")
            entry = next((e for e in registry if e["action_id"] == action_id), None)
            if entry is None:
                raise SandboxConflictError(f"no applied action {action_id}")
            filename, _ = TABLES[entry["table"]]
            rows = [row for row in self._read(filename) if row.get(entry["key"]) != entry["key_value"]]
            self._write(filename, rows)
            self._write("applied_actions.json", [e for e in registry if e["action_id"] != action_id])
            self._append_audit_locked({"event": "rolled_back", "action_id": action_id, "kind": entry["kind"],
                                       "plan_id": entry["plan_id"], "summary": entry["summary"], "user_id": user_id})
        return entry

    def log_rejection(self, *, action_id: str, kind: str, plan_id: str, summary: str, user_id: str) -> None:
        """Record that a proposal was rejected. Writes no ledger data, only the audit log."""
        with self._locked():
            self._append_audit_locked({"event": "rejected", "action_id": action_id, "kind": kind,
                                       "plan_id": plan_id, "summary": summary, "user_id": user_id})

    def _append_audit_locked(self, event: dict[str, Any]) -> None:
        """Append one event. Caller must already hold the lock."""
        events = self._read("audit_log.json")
        events.append({"event_id": uuid.uuid4().hex[:12], "timestamp": self._clock().isoformat(), **event})
        self._write("audit_log.json", events)
