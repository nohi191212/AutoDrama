from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from autodrama.core.errors import ProviderBadResponseError


class ImageSubmissionBudgetExceededError(ProviderBadResponseError):
    """Raised before a provider POST would exceed its persisted submission budget."""


@dataclass(frozen=True, slots=True)
class ImageSubmissionTicket:
    submission_id: str
    sequence: int
    limit: int


class ImageSubmissionLedger:
    """Append-only, process-safe ledger for actual provider submission attempts.

    A reservation is persisted immediately before the HTTP POST. Reservations are
    never refunded: a transport failure may still have reached the provider and
    may therefore be billable. Later lifecycle events share the same submission ID.
    """

    SCHEMA_VERSION = 1
    _thread_locks_guard = threading.Lock()
    _thread_locks: dict[str, threading.Lock] = {}

    def __init__(self, path: Path, *, limit: int) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        self.limit = int(limit)
        if self.limit < 1:
            raise ValueError("Image submission budget limit must be at least 1")

    def reserve(self, *, metadata: dict[str, Any]) -> ImageSubmissionTicket:
        with self._locked():
            used = self._reserved_count_unlocked()
            if used >= self.limit:
                raise ImageSubmissionBudgetExceededError(
                    f"Image submission budget exhausted: {used}/{self.limit} actual POST attempt(s) "
                    f"already reserved in {self.path}"
                )
            ticket = ImageSubmissionTicket(
                submission_id=uuid.uuid4().hex,
                sequence=used + 1,
                limit=self.limit,
            )
            self._append_unlocked(
                {
                    "schema_version": self.SCHEMA_VERSION,
                    "event": "submission_reserved",
                    "status": "reserved",
                    "timestamp": self._timestamp(),
                    "submission_id": ticket.submission_id,
                    "sequence": ticket.sequence,
                    "limit": ticket.limit,
                    **self._safe_metadata(metadata),
                }
            )
            return ticket

    def record_status(
        self,
        ticket: ImageSubmissionTicket | None,
        *,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if ticket is None:
            return
        clean_status = str(status or "").strip()
        if not clean_status:
            raise ValueError("Image submission status must not be empty")
        event = {
            "schema_version": self.SCHEMA_VERSION,
            "event": "submission_status",
            "status": clean_status,
            "timestamp": self._timestamp(),
            "submission_id": ticket.submission_id,
            "sequence": ticket.sequence,
            "limit": ticket.limit,
        }
        if details:
            event["details"] = self._json_safe(details)
        with self._locked():
            self._append_unlocked(event)

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            events = self._read_events_unlocked()
        reservations = [event for event in events if event.get("event") == "submission_reserved"]
        latest_status: dict[str, str] = {}
        for event in events:
            submission_id = str(event.get("submission_id") or "")
            status = str(event.get("status") or "")
            if submission_id and status:
                latest_status[submission_id] = status
        used = len(reservations)
        return {
            "path": str(self.path),
            "limit": self.limit,
            "used": used,
            "remaining": max(0, self.limit - used),
            "latest_status": latest_status,
            "event_count": len(events),
        }

    def _reserved_count_unlocked(self) -> int:
        return sum(
            1
            for event in self._read_events_unlocked()
            if event.get("event") == "submission_reserved"
        )

    def _read_events_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Image submission ledger is corrupt at {self.path}:{line_number}: {exc}"
                    ) from exc
                if not isinstance(event, dict):
                    raise RuntimeError(
                        f"Image submission ledger event is not an object at {self.path}:{line_number}"
                    )
                events.append(event)
        return events

    def _append_unlocked(self, event: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_key = str(self.lock_path.resolve())
        with self._thread_locks_guard:
            thread_lock = self._thread_locks.setdefault(lock_key, threading.Lock())
        with thread_lock:
            with self.lock_path.open("a+b") as handle:
                self._acquire_file_lock(handle)
                try:
                    yield
                finally:
                    self._release_file_lock(handle)

    @staticmethod
    def _acquire_file_lock(handle: Any) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _release_file_lock(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "provider",
            "model",
            "node_name",
            "asset_id",
            "prompt_asset_type",
            "prompt_asset_name",
            "provider_attempt",
            "safety_prompt_rewrite_attempt",
        )
        return {
            key: ImageSubmissionLedger._json_safe(metadata[key])
            for key in allowed
            if metadata.get(key) is not None
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): ImageSubmissionLedger._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ImageSubmissionLedger._json_safe(item) for item in value]
        return str(value)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ImageSubmissionBudgetExceededError",
    "ImageSubmissionLedger",
    "ImageSubmissionTicket",
]
