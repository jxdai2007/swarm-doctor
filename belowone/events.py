"""Event schema and the shared, credential-safe artifact JSON boundary."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping


_SECRET_NAMES = ('apikey', 'authorization', 'accesstoken', 'refreshtoken',
                 'bearertoken', 'authtoken', 'sessiontoken', 'password',
                 'secret', 'privatekey', 'credential')
_INLINE_SECRET = re.compile(
    r'(?i)\b(?:proxy-)?authorization\s*:\s*[^\r\n]*|'
    r'\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)'
    r'\s*[:=]\s*[\"\']?[^\s&,;\"\'{}]+|\bBearer\s+[^\s\"\'&,;]+')
_KEY_VALUE = re.compile(r'\b(?:sk-(?:or-v1-|ant-)?)[A-Za-z0-9_-]{16,}\b')
REDACTED = '[REDACTED]'


def _name(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.lower())


def _sensitive(value: str) -> bool:
    normalized = _name(value)
    return normalized in {'key', 'token'} or any(normalized.endswith(name) for name in _SECRET_NAMES)


def _headers(value: str) -> bool:
    return _name(value).endswith('headers')


def scrub(value: Any) -> Any:
    """Remove header/credential fields, then redact their values everywhere.

    Environment credentials and common inline key/header formats are also
    scrubbed. Call this before every config, cache, log, or metrics serialization.
    Arbitrary unlabelled unknown secrets cannot be identified by any serializer.
    """
    secrets = {item for key, item in os.environ.items()
               if item and (_sensitive(key) or key.upper().endswith(('_KEY', '_TOKEN', '_SECRET')))}

    def collect(item: Any, private: bool = False) -> None:
        if is_dataclass(item) and not isinstance(item, type):
            item = asdict(item)
        if isinstance(item, Mapping):
            for key, child in item.items():
                collect(child, private or _sensitive(str(key)) or _headers(str(key)))
        elif isinstance(item, (list, tuple)):
            for child in item:
                collect(child, private)
        elif private and isinstance(item, str) and item:
            secrets.add(item)
            if item.lower().startswith('bearer '):
                secrets.add(item[7:])

    collect(value)
    # Longest first prevents a short credential from exposing a longer suffix.
    replacements = sorted(secrets, key=len, reverse=True)

    def text(item: str) -> str:
        for secret in replacements:
            item = item.replace(secret, REDACTED)
        return _KEY_VALUE.sub(REDACTED, _INLINE_SECRET.sub(REDACTED, item))

    def clean(item: Any) -> Any:
        if is_dataclass(item) and not isinstance(item, type):
            item = asdict(item)
        if isinstance(item, Mapping):
            if not all(isinstance(key, str) for key in item):
                raise TypeError('artifact JSON object keys must be strings')
            return {text(key): clean(child) for key, child in item.items()
                    if not _sensitive(key) and not _headers(key)}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, str):
            return text(item)
        if isinstance(item, (Decimal, Path)):
            return text(str(item))
        return item

    return clean(value)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')


def artifact_bytes(value: Any) -> bytes:
    """Canonical UTF-8 JSON with final newline and no credentials or headers."""
    return _canonical_bytes(scrub(value))


def payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class Event:
    agent_id: str
    seq: int
    timestamp: str
    monotonic: float
    kind: str
    paths: list[str]
    content_hash: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        result = scrub(asdict(self))
        result['content_hash'] = payload_hash(result['payload'])
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> 'Event':
        event = cls(**value)
        if not isinstance(event.agent_id, str) or not event.agent_id:
            raise ValueError('event agent_id must be a nonempty string')
        if type(event.seq) is not int or event.seq < 1:
            raise ValueError('event seq must be a positive integer')
        if not isinstance(event.kind, str) or not event.kind:
            raise ValueError('event kind must be a nonempty string')
        if not isinstance(event.paths, list) or not all(isinstance(path, str) for path in event.paths):
            raise ValueError('event paths must be a list of strings')
        if not isinstance(event.payload, dict):
            raise ValueError('event payload must be an object')
        if type(event.monotonic) not in (float, int) or not math.isfinite(event.monotonic) or event.monotonic < 0:
            raise ValueError('event monotonic must be finite and nonnegative')
        if not isinstance(event.timestamp, str) or datetime.fromisoformat(event.timestamp).tzinfo is None:
            raise ValueError('event timestamp must include a timezone')
        if event.content_hash != payload_hash(event.payload):
            raise ValueError('event payload hash mismatch')
        return event
