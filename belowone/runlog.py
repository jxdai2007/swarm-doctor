"""Durable append-only JSONL log, shared by live execution and replay."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
import warnings

from belowone.events import Event, _canonical_bytes, payload_hash, scrub


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)

    @staticmethod
    def _read(stream) -> tuple[list[Event], bool]:
        events = []
        stream.seek(0)
        for number, line in enumerate(stream, 1):
            if not line.endswith(b'\n'):
                warnings.warn('event log truncated final line skipped', RuntimeWarning, stacklevel=3)
                return events, True
            try:
                event = Event.from_dict(json.loads(line))
                if event.seq != len(events) + 1:
                    raise ValueError('event sequence is not contiguous')
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError(f'invalid event log line {number}') from exc
            events.append(event)
        return events, False

    def read(self) -> list[Event]:
        with self.path.open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            try:
                return self._read(stream)[0]
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def append(self, agent_id: str, kind: str, paths: Sequence[str] | None = None,
               payload: Mapping[str, Any] | None = None) -> Event:
        # ponytail: O(n) scan per append; indexed tail metadata if logs outgrow run budgets.
        # O_APPEND and an inode lock protect sequence assignment across processes/instances.
        with self.path.open('a+b') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                existing, truncated = self._read(stream)
                if truncated:
                    raise ValueError('cannot append to a crash-truncated log; preserve it and start a new run')
                data = scrub({'agent_id': agent_id, 'kind': kind,
                              'paths': list(paths) if paths is not None else [],
                              'payload': dict(payload) if payload is not None else {}})
                event = Event.from_dict(data | {'seq': len(existing) + 1,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'monotonic': time.monotonic(), 'content_hash': payload_hash(data['payload'])})
                stream.write(_canonical_bytes(event.to_dict()))
                stream.flush()
                os.fsync(stream.fileno())
                return event
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
