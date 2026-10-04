#!/usr/bin/env python3
"""Byte-compare full authored documents and generated metrics against sources.

Regenerate intentionally with ``make regenerate``; this checker never repairs
or rewrites published files. Missing source metrics fail closed.
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from belowone.viz.doc_templates import (  # noqa: E402
    AUTHORED_TEMPLATES, build_docs, compare_authored, load_metrics,
)


def check_links(root: Path) -> list[str]:
    failures = []
    for name in AUTHORED_TEMPLATES:
        path = root / name
        if not path.is_file():
            continue  # full-document comparison reports missing files
        text = path.read_text()
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            clean = target.split("#", 1)[0]
            if clean and not (path.parent / clean).resolve().exists():
                failures.append(f"{name}: broken link {target}")
        if name == "docs/writeup.md" and (
                "[unmeasured]" not in text or "[SYNTHETIC DEV]" not in text):
            failures.append(f"{name}: missing explicit scientific status markers")
        if name == "docs/video-script.md":
            for clip in set(re.findall(r"clips/[\w./-]+\.webm", text)):
                if not (root / clip).is_file():
                    failures.append(f"{name}: missing clip {clip}")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics-glob", default="experiments/derived/analysis.json")
    parser.add_argument("--docs", default="docs/generated")
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="Submission root containing the five authored documents")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    pattern = args.metrics_glob
    metric_files = sorted(Path(p) for p in glob.glob(
        pattern if Path(pattern).is_absolute() else str(root / pattern)))
    if not metric_files:
        print("DOC_DIFF: no metric sources matched --metrics-glob")
        return 1
    docs = Path(args.docs)
    if not docs.is_absolute():
        docs = root / docs
    try:
        metrics = load_metrics(metric_files)
        failures = compare_authored(metrics, root)
        with tempfile.TemporaryDirectory(prefix="belowone-doc-check-") as temporary:
            for path in build_docs(metrics, Path(temporary)):
                committed = docs / path.name
                if not committed.is_file() or committed.read_bytes() != path.read_bytes():
                    failures.append(f"{committed}: missing or byte-different generated document")
        failures.extend(check_links(root))
    except (KeyError, ValueError, OSError) as error:
        failures = [f"invalid source or document: {error}"]
    for failure in failures:
        print("DOC_DIFF:", failure)
    if failures:
        return 1
    print("DOC_SOURCES_VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
