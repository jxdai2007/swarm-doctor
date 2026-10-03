#!/usr/bin/env python3
"""KTD14 doc-number check (U17): regenerate the metric-derived docs from the
committed metrics artifacts and byte-compare against the committed files.
Any edited number fails with the differing file (and line, when the doc has
one). Exit 0 = every number is source-bound.

Usage: uv run python scripts/check_doc_numbers.py [--docs docs/generated]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from belowone.viz.doc_templates import build_docs, load_metrics  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics-glob", default="experiments/committed/runs/*/metrics.json")
    parser.add_argument("--docs", default="docs/generated")
    args = parser.parse_args(argv)

    import glob as globmod
    pattern = args.metrics_glob
    metric_files = sorted(Path(p) for p in globmod.glob(pattern)) \
        if Path(pattern.split("/*")[0]).is_absolute() \
        else sorted(ROOT.glob(pattern))
    if not metric_files:
        print("no committed metrics artifacts found under", args.metrics_glob)
        return 1
    metrics = load_metrics(
        [p for p in metric_files if p.name in ("metrics.json",
                                               "snapshot.json")])
    docs = Path(args.docs)
    if not docs.is_absolute():
        docs = ROOT / docs
    regenerated = docs / ".regen"
    written = build_docs(metrics, regenerated)
    failures = []
    for path in written:
        committed = docs / path.name
        if not committed.is_file():
            failures.append(f"{committed}: missing (regenerate and commit "
                            "via scripts/check_doc_numbers.py --write)")
        elif committed.read_bytes() != path.read_bytes():
            for lineno, (a, b) in enumerate(zip(
                    committed.read_text().splitlines(),
                    path.read_text().splitlines()), 1):
                if a != b:
                    failures.append(f"{committed}:{lineno}: committed "
                                    f"{a.strip()!r} != regenerated "
                                    f"{b.strip()!r}")
                    break
    if failures:
        for f in failures:
            print("DOC_DIFF:", f)
        return 1

    # --- authored docs (README, writeup, threat model, capability, script) --
    import re as _re
    authored = [ROOT / "README.md", ROOT / "docs" / "writeup.md",
                ROOT / "docs" / "threat-model.md",
                ROOT / "docs" / "capability-table.md",
                ROOT / "docs" / "video-script.md"]
    for path in authored:
        if not path.is_file():
            failures.append(f"{path.relative_to(ROOT)}: missing")
            continue
        text = path.read_text()
        # (a) unrendered metric tokens must not survive into authored prose
        for token in _re.findall(r"\{[a-z0-9-]+\.[a-z0-9_.]+\}", text):
            failures.append(f"{path.relative_to(ROOT)}: unrendered metric "
                            f"token {token}")
        # (b) markdown links to repo files must resolve
        for target in _re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://")):
                continue
            clean = target.split("#")[0]
            if clean and not (path.parent / clean).resolve().exists() \
                    and not (ROOT / clean).exists():
                failures.append(f"{path.relative_to(ROOT)}: broken link "
                                f"{target}")
        # (c) the writeup must carry explicit unmeasured/synthetic markers
        if path.name == "writeup.md":
            if "[unmeasured]" not in text or "[SYNTHETIC DEV]" not in text:
                failures.append("docs/writeup.md: missing explicit "
                                "[unmeasured]/[SYNTHETIC DEV] markers")
        # (d) video-script clip references must exist on disk
        if path.name == "video-script.md":
            for clip in set(_re.findall(r"clips/[\w.-]+\.webm", text)):
                if not (ROOT / clip).is_file():
                    failures.append(f"docs/video-script.md: clip {clip} "
                                    "does not exist")
    if failures:
        for f in failures:
            print("DOC_DIFF:", f)
        return 1
    print("DOC_SOURCES_VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
