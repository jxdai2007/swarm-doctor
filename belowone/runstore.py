"""Per-run artifacts with byte-exact SHA-256 seals and inventory verification."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Mapping

from belowone.events import _canonical_bytes, artifact_bytes, scrub


_RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


class RunStore:
    """Keep the seal digest outside each run so editing its manifest is detected.

    A seal detects file bytes, file/directory additions and removals, and symlinks.
    It is not a signature against an attacker who can also edit the store's
    private .seals registry. Archive that registry alongside sealed run folders.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._seals = self.root / '.seals'
        if self._seals.is_symlink():
            raise ValueError('seal registry must not be a symlink')
        self._seals.mkdir(mode=0o700, exist_ok=True)

    @contextmanager
    def _lock(self, exclusive: bool = True):
        with (self._seals / '.lock').open('a+b') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _run(self, path: str | Path) -> Path:
        run = Path(path).absolute()
        if not _RUN_ID.fullmatch(run.name) or run.parent != self.root or run.is_symlink() or not run.is_dir():
            raise ValueError('run must be a direct nonsymlink directory in this store')
        return run

    def _seal_path(self, run: Path) -> Path:
        return self._seals / (run.name + '.sha256')

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        with path.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def create(self, run_id: str, config: Mapping[str, Any], commit: str) -> Path:
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            raise ValueError('invalid run ID')
        if scrub(run_id) != run_id:
            raise ValueError('run ID contains credential material')
        if not isinstance(config, Mapping):
            raise ValueError('run config must be an object')
        if not isinstance(commit, str) or not commit or '\n' in commit or '\r' in commit:
            raise ValueError('commit must be one nonempty line')
        config_bytes = artifact_bytes(config)
        commit_bytes = (scrub(commit) + '\n').encode('utf-8')
        run = self.root / run_id
        with self._lock():
            if self._seal_path(run).exists():
                raise ValueError('run ID already sealed')
            run.mkdir(mode=0o700)
            for name in ('cache', 'metrics', 'figures', 'manifests'):
                (run / name).mkdir()
            self._write(run / 'config.json', config_bytes)
            self._write(run / 'commit.txt', commit_bytes)
            self._write(run / 'events.jsonl', b'')
        return run

    def write_json(self, path: str | Path, relative: str | Path, value: Any) -> Path:
        """Write a sanitized config/cache/metrics/manifest artifact before sealing."""
        content = artifact_bytes(value)
        with self._lock():
            run = self._run(path)
            if self._seal_path(run).exists() or (run / 'manifest.json').exists():
                raise ValueError('run already sealed')
            name = Path(relative)
            if name.is_absolute() or '..' in name.parts or name == Path('.') or name == Path('manifest.json'):
                raise ValueError('invalid artifact path')
            if scrub(name.as_posix()) != name.as_posix():
                raise ValueError('artifact filename contains credential material')
            destination = run / name
            if any(part.is_symlink() for part in [destination, *destination.parents] if part != self.root):
                raise ValueError('artifact path contains a symlink')
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Replace only after complete serialization; atomic rename prevents partial JSON.
            descriptor, temporary_name = tempfile.mkstemp(prefix='.artifact-', dir=destination.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return destination

    @staticmethod
    def _inventory(run: Path) -> dict[str, Any]:
        files = {}
        directories = []
        for path in sorted(run.rglob('*')):
            name = path.relative_to(run).as_posix()
            if scrub(name) != name:
                raise ValueError('artifact filename contains credential material')
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError('run artifact contains a symlink')
            if stat.S_ISDIR(mode):
                directories.append(name)
            elif stat.S_ISREG(mode):
                if name != 'manifest.json':
                    digest = hashlib.sha256()
                    with path.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(chunk)
                    files[name] = digest.hexdigest()
            else:
                raise ValueError('run artifact must be a regular file or directory')
        return {'version': 1, 'files': files, 'directories': directories}

    def seal(self, path: str | Path) -> Path:
        with self._lock():
            run = self._run(path)
            manifest_path = run / 'manifest.json'
            seal_path = self._seal_path(run)
            if seal_path.exists() or manifest_path.exists():
                raise ValueError('run already sealed')
            # Inventory has no arbitrary payload fields; path strings were scrub-validated.
            content = _canonical_bytes(self._inventory(run))
            self._write(manifest_path, content)
            self._write(seal_path, (hashlib.sha256(content).hexdigest() + '\n').encode('ascii'))
            return manifest_path

    def verify(self, path: str | Path) -> bool:
        """Return False for any unsealed, missing, changed, or unsafe artifact."""
        try:
            with self._lock(exclusive=False):
                run = self._run(path)
                manifest_path = run / 'manifest.json'
                seal_path = self._seal_path(run)
                if manifest_path.is_symlink() or seal_path.is_symlink():
                    return False
                content = manifest_path.read_bytes()
                if seal_path.read_bytes() != (hashlib.sha256(content).hexdigest() + '\n').encode('ascii'):
                    return False
                # Parsing also rejects malformed JSON; compare exact serialized inventory.
                manifest = json.loads(content)
                inventory = self._inventory(run)
                if manifest['files'] != inventory['files']:
                    return False
                # Empty declared directories cannot survive git transport: validate
                # a declared directory only when it physically exists (absence is
                # valid because no payload key can live under an empty dir).
                for name in manifest['directories']:
                    path = run / name
                    if not path.exists():
                        continue
                    if not path.is_dir() or path.is_symlink() or scrub(name) != name:
                        return False
                if not set(inventory['directories']) <= set(manifest['directories']):
                    return False
                return content == _canonical_bytes(manifest)
        except (OSError, ValueError, TypeError):
            return False
