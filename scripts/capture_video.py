#!/usr/bin/env python3
"""U13 beat capture: records every storyboard beat from REAL surfaces —
the engine board, actual CLI/make output rendered as a terminal page, actual
generated figures and receipts. All clips labeled SYNTHETIC DEV.

Usage: uv run python scripts/capture_video.py --base http://127.0.0.1:8899
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SECONDS = {"intro-board": 10, "two-dials": 10, "scripted-interview": 15,
           "live-catch": 15, "split-race": 20, "charts": 10,
           "receipt": 10, "reproduce-proof": 15, "everyday": 20,
           "threat": 5, "repo-close": 5}


def page(title: str, body: str) -> str:
    return ("<!doctype html><meta charset='utf-8'>"
            "<title>" + html.escape(title) + "</title><style>"
            "body{background:#101418;color:#e8edf2;font:16px/1.5 monospace;"
            "padding:40px}pre{white-space:pre-wrap}h1{font-size:20px}"
            ".badge{color:#ffd27a;border:1px solid #5a4a22;border-radius:4px;"
            "padding:2px 8px;display:inline-block;margin-bottom:16px}"
            "img{max-width:900px;display:block;margin:12px 0}"
            "</style><span class='badge'>SYNTHETIC DEV — development "
            "recording, not live evidence</span><h1>" + html.escape(title)
            + "</h1>" + body)


def terminal(text: str) -> str:
    return page("terminal", "<pre>" + html.escape(text) + "</pre>")


def build_pages(base: str) -> dict[str, str]:
    pages = Path(ROOT / "clips" / "_pages")
    pages.mkdir(parents=True, exist_ok=True)

    def write(name: str, html_text: str) -> str:
        path = pages / f"{name}.html"
        path.write_text(html_text)
        return path.as_uri()

    urls = {}

    readme = (ROOT / "README.md").read_text()
    dials = readme.split("## Quick start")[0].split("## Measured")[0]
    urls["two-dials"] = write("two-dials.html",
                              page("Two dials", "<pre>"
                                   + html.escape(dials) + "</pre>"))

    cli = subprocess.run(
        ["uv", "run", "python", "-m", "belowone.cli", "interview",
         "--task", "/tmp/u11out/task.md", "--workspace", "/tmp/u11out",
         "--scripted", "/tmp/u11out/answers.json",
         "--out", "/tmp/u11out/goal-spec.json", "--model-mode", "synthetic"],
        capture_output=True, text=True, cwd=ROOT)
    urls["scripted-interview"] = write(
        "interview.html",
        terminal("$ uv run python -m belowone.cli interview --scripted … "
                 "(model-mode synthetic)\n"
                 + (cli.stdout + cli.stderr).strip()))

    repro = subprocess.run(["make", "reproduce"], capture_output=True,
                           text=True, cwd=ROOT)
    urls["reproduce-proof"] = write(
        "repro.html", terminal("$ make reproduce\n"
                               + (repro.stdout + repro.stderr).strip()))

    figures = ROOT / "docs" / "generated" / "figures"
    body = "".join(
        f"<img src='file://{figures / n}.svg'>" for n in
        ("epidemic", "r-bar", "delay-damage"))
    urls["charts"] = write("charts.html", page("Doc figures", body))

    from belowone.eval.metrics import outbreak_metrics
    from belowone.eval.replay import replay_freeze_schedule
    from belowone.viz.receipts import receipt, render
    from types import SimpleNamespace
    run = ROOT / "experiments/committed/runs/pilot-0"
    events = []
    for line in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        events.append(SimpleNamespace(seq=e["seq"], agent_id=e.get("agent_id"),
                                      kind=e.get("kind"),
                                      paths=e.get("paths") or [],
                                      payload=e.get("payload") or {}))
    freezes = sorted({(e.agent_id, float(e.payload.get("elapsed", 0)))
                      for e in events if e.kind == "freeze"})
    om = outbreak_metrics(replay_freeze_schedule(events, freezes), events,
                          seed=0)
    receipt_text = render(receipt(
        "below-one-verify (SYNTHETIC DEV)", om,
        {"patient_zero": "a0", "catching_layer": "tripwire",
         "cost_usd": 0.02, "synthetic": True}))
    urls["receipt"] = write("receipt.html", page("Outbreak receipt",
                                                 "<pre>" + receipt_text
                                                 + "</pre>"))

    threat = (ROOT / "docs" / "threat-model.md").read_text()
    section = threat.split("## Trust")[0]
    urls["threat"] = write("threat.html",
                           page("Threat model — blind spots",
                                "<pre>" + html.escape(section) + "</pre>"))

    close = (ROOT / "README.md").read_text().split("## Measured")[0]
    urls["repo-close"] = write("close.html",
                               page("Below One",
                                    "<pre>" + html.escape(close) + "</pre>"))
    return urls


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8899")
    parser.add_argument("--out", default="clips")
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright missing: uv add playwright && uv run playwright install chromium")

    out = ROOT / args.out
    out.mkdir(exist_ok=True)
    urls = build_pages(args.base)
    beats = [
        ("intro-board-SYNTHETIC-DEV", f"{args.base}/?run=live"),
        ("two-dials-SYNTHETIC-DEV", urls["two-dials"]),
        ("scripted-interview-SYNTHETIC-DEV", urls["scripted-interview"]),
        ("live-catch-SYNTHETIC-DEV",
         f"{args.base}/?run=below-one-verify-fixture-outbreak"),
        ("split-race-SYNTHETIC-DEV",
         f"{args.base}/?mode=split&run=below-one-verify-fixture-outbreak"),
        ("charts-SYNTHETIC-DEV", urls["charts"]),
        ("receipt-SYNTHETIC-DEV", urls["receipt"]),
        ("reproduce-proof-SYNTHETIC-DEV", urls["reproduce-proof"]),
        ("everyday-SYNTHETIC-DEV",
         f"{args.base}/?run=below-one-verify-fixture-outbreak"),
        ("threat-SYNTHETIC-DEV", urls["threat"]),
        ("repo-close-SYNTHETIC-DEV", urls["repo-close"]),
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, url in beats:
            seconds = next((s for key, s in SECONDS.items()
                            if name.startswith(key)), 10)
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(out / "_raw"),
                record_video_size={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(int(seconds * 1000))
            context.close()
            saved = sorted((out / "_raw").glob("*.webm"),
                           key=lambda p: p.stat().st_mtime)[-1]
            target = out / f"{name}.webm"
            shutil.move(str(saved), target)
            print(f"captured {target.name} ({target.stat().st_size} bytes, "
                  f"{seconds}s)")
        browser.close()
    shutil.rmtree(out / "_raw", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
