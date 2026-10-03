"""Pinned development scenarios and manifest-defined ground truth.

These APIs prepare throwaway agent workspaces, not live experiment evidence.
The grader is process-isolated against workspace import hijacking, not an OS sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent


def load_manifest(root: str | Path = ROOT) -> dict[str, Any]:
    """Load the JSON-subset YAML manifest, validating its pinned file inventory."""
    root = Path(root)
    manifest = json.loads((root / "manifest.yaml").read_text())
    if manifest.get("version") != 1:
        raise ValueError("unsupported scenario manifest version")
    check_checksums(root, manifest)
    return manifest


def check_checksums(root: str | Path = ROOT, manifest: dict | None = None) -> None:
    """Reject missing, added, symlinked, or changed files; manifest excludes itself."""
    root = Path(root)
    if manifest is None:
        manifest = json.loads((root / "manifest.yaml").read_text())
    files = manifest["checksums"]
    actual = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store"
        and p != root / "manifest.yaml"
    }
    if actual != set(files):
        raise ValueError(f"scenario inventory mismatch: {sorted(actual ^ set(files))}")
    for relative, digest in files.items():
        path = root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError(f"unsafe scenario path: {relative}")
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"invalid scenario file: {relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"scenario checksum mismatch: {relative}")


def load_scenario(name: str, root: str | Path = ROOT) -> dict[str, Any]:
    """Return independent scenario metadata including a GoalSpec-compatible spec."""
    manifest = load_manifest(root)
    try:
        return manifest["scenarios"][name]
    except KeyError:
        raise ValueError(f"unknown scenario: {name}") from None


def prepare_scenario(name: str, destination: str | Path, *, root: str | Path = ROOT,
                     reference: bool = False) -> Path:
    """Copy base plus variant overlays into a new, non-repository workspace."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    scenario = load_scenario(name, root)
    if destination == root.parent or root.parent in destination.parents:
        raise ValueError("workspace must be outside the scenario repository")
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(root / "base", destination)
    for overlay in scenario["overlays"]:
        shutil.copytree(root / overlay, destination, dirs_exist_ok=True)
    if reference:
        shutil.copyfile(root / "grader/reference_export.py", destination / "reports/export.py")
    return destination


def copy_scenario(name: str, destination: str | Path, *, root: str | Path = ROOT,
                  reference: bool = False) -> Path:
    """Named copy API for harness callers; same contract as prepare_scenario."""
    return prepare_scenario(name, destination, root=root, reference=reference)


def check_scenario(workspace: str | Path, *, root: str | Path = ROOT,
                   timeout: float = 10.0) -> dict[str, Any]:
    """Run trusted held-out grader outside agent loading paths. Return verdict JSON.

    No workspace test, pass marker, package initializer, sitecustomize, or PYTHONPATH
    participates in grading. Hard-line edits to protected tests fail independently.
    Callers must enforce their normal filesystem/network sandbox for hostile code.
    """
    root = Path(root).resolve()
    load_manifest(root)
    workspace = Path(workspace).resolve()
    with tempfile.TemporaryDirectory(prefix="belowone-grader-") as temporary:
        trusted = Path(temporary)
        for name in ("check.py", "runner.py"):
            shutil.copyfile(root / "grader" / name, trusted / name)
        request = {
            "workspace": str(workspace),
            "protected": {
                "tests/test_reports.py": hashlib.sha256(
                    (root / "base/tests/test_reports.py").read_bytes()).hexdigest()
            },
        }
        process = subprocess.Popen(
            [sys.executable, "-I", str(trusted / "check.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=trusted, start_new_session=True,
            env={"PATH": str(Path(sys.executable).parent)},
        )
        try:
            stdout, _ = process.communicate(json.dumps(request), timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the whole grader group, including a hanging candidate child.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            return {"passed": False, "reason": "grader timeout"}
        if process.returncode:
            return {"passed": False, "reason": "grader process failed"}
        try:
            verdict = json.loads(stdout)
            if not isinstance(verdict, dict) or not isinstance(verdict.get("passed"), bool):
                raise ValueError("invalid verdict")
            return verdict
        except (ValueError, TypeError):
            return {"passed": False, "reason": "invalid grader output"}
