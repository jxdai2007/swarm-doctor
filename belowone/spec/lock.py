"""Canonical hashes and atomic operator-owned locked-spec persistence."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile

from belowone.spec.schema import GoalSpec, SpecValidationError, workspace_path


class SpecLockError(ValueError):
    """A lock is malformed, modified, or differs from the session pin."""


def canonical_json(spec: GoalSpec) -> bytes:
    if not isinstance(spec, GoalSpec):
        raise TypeError("canonical_json requires GoalSpec")
    return json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def spec_hash(spec: GoalSpec) -> str:
    """SHA-256 of canonical UTF-8 JSON; mapping order/formatting are irrelevant."""
    return hashlib.sha256(canonical_json(spec)).hexdigest()


def _lock_path(path, workspace):
    root = Path(workspace).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    if target.is_symlink():
        raise SpecValidationError("locked spec path must not be a symlink")
    # Canonicalize parent aliases (macOS /var -> /private/var), not final symlink.
    target = target.parent.resolve() / target.name
    try:
        relative = target.relative_to(root).as_posix()
    except ValueError as error:
        raise SpecValidationError("locked spec path must stay inside workspace") from error
    workspace_path(relative, root, "locked spec path")
    if target == root:
        raise SpecValidationError("locked spec path must name a file")
    return target


def lock_spec(spec: GoalSpec, path, *, operator_confirmed=False) -> str:
    """Write ``{spec, hash}``, returning the session pin.

    Initial creation assumes the operator interview already confirmed this spec.
    Replacing an existing lock requires explicit operator re-confirmation. Agents
    must never invoke this operator-only function; filesystem mode is read-only,
    not an authentication mechanism. Engine tripwires protect the lock path and
    startup passes the retained hash to ``load_locked_spec(expected_hash=...)``.
    """
    if not isinstance(spec, GoalSpec):
        raise TypeError("lock_spec requires GoalSpec")
    if type(operator_confirmed) is not bool:
        raise TypeError("operator_confirmed must be a boolean")
    target = _lock_path(path, spec._workspace)
    if target.exists() and not operator_confirmed:
        raise SpecLockError("locked spec already exists; operator re-confirmation required")
    digest = spec_hash(spec)
    content = json.dumps({"hash": digest, "spec": spec.to_dict()}, sort_keys=True,
                         ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".spec-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o444)
        if operator_confirmed:
            os.replace(temporary, target)
        else:
            # Publish complete bytes atomically without a check-then-overwrite race.
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise SpecLockError("locked spec already exists; operator re-confirmation required") from error
    finally:
        Path(temporary).unlink(missing_ok=True)
    return digest


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SpecLockError(f"duplicate locked spec key: {key}")
        result[key] = value
    return result


def load_locked_spec(path, *, workspace=None, expected_hash=None) -> GoalSpec:
    """Validate lock contents and optional trusted session pin before startup.

    An embedded digest detects file tampering, not an attacker who rewrites both
    spec and digest. ``expected_hash`` must come from trusted operator/run state,
    never from this same file. Workspace defaults to the caller's current directory.
    """
    root = Path.cwd() if workspace is None else Path(workspace)
    target = _lock_path(path, root)
    try:
        content = json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SpecLockError(f"cannot read locked spec: {error}") from error
    if not isinstance(content, dict) or set(content) != {"spec", "hash"}:
        raise SpecLockError("locked spec must contain exactly spec and hash")
    digest = content["hash"]
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise SpecLockError("locked spec hash must be a lowercase SHA-256 digest")
    if expected_hash is not None:
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or any(char not in "0123456789abcdef" for char in expected_hash):
            raise SpecLockError("expected_hash must be a lowercase SHA-256 digest")
        if not hmac.compare_digest(expected_hash, digest):
            raise SpecLockError("locked spec hash mismatch with session pin")
    try:
        spec = GoalSpec.from_dict(content["spec"], workspace=root)
    except SpecValidationError as error:
        raise SpecLockError(f"invalid locked spec: {error}") from error
    if set(content["spec"]) != set(spec.to_dict()):
        raise SpecLockError("locked spec must contain every canonical field")
    if not hmac.compare_digest(digest, spec_hash(spec)):
        raise SpecLockError("locked spec hash mismatch")
    return spec
