#!/usr/bin/env python3
"""Headless video capture (U13): Playwright-recorded dashboard beats.
Output clips labeled SYNTHETIC DEV — fixture replay proof of the
dashboard/capture path; never live evidence, never pilot clips.

Usage: python scripts/capture_video.py [--base http://127.0.0.1:8765] [--out clips]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--out", default="clips")
    parser.add_argument("--seconds", type=float, default=14.0)
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright missing: uv add playwright && uv run playwright install chromium")

    out = ROOT / args.out
    out.mkdir(exist_ok=True)
    beats = [
        ("live-catch-SYNTHETIC-DEV",
         f"{args.base}/?run=below-one-verify-fixture-outbreak"),
        ("split-race-SYNTHETIC-DEV",
         f"{args.base}/?mode=split&seed=fixture-outbreak"),
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, url in beats:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(out / "_raw"),
                record_video_size={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(int(args.seconds * 1000))
            context.close()  # flushes the video
            saved = sorted((out / "_raw").glob("*.webm"),
                           key=lambda p: p.stat().st_mtime)[-1]
            target = out / f"{name}.webm"
            shutil.move(str(saved), target)
            print(f"captured {target} (SYNTHETIC DEV fixture replay, "
                  f"{target.stat().st_size} bytes)")
        browser.close()
    shutil.rmtree(out / "_raw", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
