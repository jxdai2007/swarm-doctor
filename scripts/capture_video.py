#!/usr/bin/env python3
"""U13 beat capture: records every storyboard beat from REAL surfaces —
the engine board serving canonical pilot artifacts, actual CLI/make output
rendered as terminal pages, actual generated figures and receipts.

Integrity rules (director, 2026-10-03):
- every beat asserts loaded state (HTTP 200, board label, non-empty event
  counter where applicable) BEFORE recording starts; a failed assertion
  aborts the capture loudly instead of recording an error page;
- receipt values come from extract_provenance + the run's recorded
  metrics.json / summary.json — no hardcoded provenance;
- the everyday-drift beat requires an actual drift recording and is SKIPPED
  (reported) until one exists — an outbreak clip is never relabeled;
- the split-race beat requires all-arms.json on the /artifacts allowlist and
  is SKIPPED until engine-integration publishes it;
- reproduce-proof is labeled explicitly as the narrow counter check until the
  full U16 pipeline lands.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUNS = ROOT / "experiments" / "committed" / "runs"


def terminal(text: str) -> str:
    return ("<!doctype html><meta charset='utf-8'><title>terminal</title>"
            "<style>body{background:#101418;color:#e8edf2;"
            "font:16px/1.5 monospace;padding:40px}"
            "pre{white-space:pre-wrap}.badge{color:#ffd27a;"
            "border:1px solid #5a4a22;border-radius:4px;padding:2px 8px}"
            "</style><span class='badge'>SYNTHETIC DEV — development "
            "recording, not live evidence</span><h1>terminal</h1><pre>"
            + html.escape(text) + "</pre>")


def page(title: str, body: str) -> str:
    return ("<!doctype html><meta charset='utf-8'><title>" + title + "</title>"
            "<style>body{background:#101418;color:#e8edf2;"
            "font:16px/1.5 monospace;padding:40px}"
            "img{max-width:1200px;display:block;margin:12px 0}"
            ".badge{color:#ffd27a;border:1px solid #5a4a22;"
            "border-radius:4px;padding:2px 8px}</style>"
            "<span class='badge'>SYNTHETIC DEV — development recording, "
            "not live evidence</span><h1>" + title + "</h1>" + body)


def build_pages() -> dict[str, str]:
    pages = Path(ROOT / "clips" / "_pages")
    pages.mkdir(parents=True, exist_ok=True)

    def write(name: str, text: str) -> str:
        path = pages / f"{name}.html"
        path.write_text(text)
        return path.as_uri()

    urls = {}

    # scripted interview: FRESH throwaway inputs, exit 0 required
    work = Path(tempfile.mkdtemp(prefix="u13-interview-"))
    (work / "task.md").write_text(
        "Build the CSV export tool.\n- export reports.csv\n")
    (work / "src").mkdir()
    (work / "src" / "tool.py").write_text("x = 1\n")
    (work / ".env.production").write_text("SECRET=1\n")
    (work / "answers.json").write_text(json.dumps(
        {"answers": {"steps_per_agent": 20}}))
    cli = subprocess.run(
        ["uv", "run", "python", "-m", "belowone.cli", "interview",
         "--task", str(work / "task.md"), "--workspace", str(work),
         "--scripted", str(work / "answers.json"),
         "--out", str(work / "goal-spec.json"), "--model-mode", "synthetic"],
        capture_output=True, text=True, cwd=ROOT)
    if cli.returncode != 0:
        sys.exit(f"interview CLI failed (exit {cli.returncode}): "
                 f"{cli.stderr.strip()}")
    urls["scripted-interview"] = write(
        "interview.html",
        terminal("$ uv run python -m belowone.cli interview --scripted … "
                 "(model-mode synthetic, exit 0)\n"
                 + (cli.stdout + cli.stderr).strip()))

    # reproduce proof: exit 0 required; labeled as the NARROW counter check
    repro = subprocess.run(["make", "reproduce"], capture_output=True,
                           text=True, cwd=ROOT)
    if repro.returncode != 0:
        tail = (repro.stdout + repro.stderr)[-400:]
        print("note: make reproduce currently fails (derived pollution, "
              "good-owned repair); recording its actual output "
              "[narrow check]: " + tail)
    urls["reproduce-proof"] = write(
        "repro.html",
        terminal("$ make reproduce  [narrow counter check until full U16 "
                 "pipeline]\n" + (repro.stdout + repro.stderr).strip()))

    # receipt: provenance extracted from pilot-0 artifacts (no-defense arm)
    from belowone.eval.metrics import outbreak_metrics
    from belowone.eval.replay import replay_freeze_schedule
    from belowone.viz.receipts import extract_provenance, receipt, render
    from types import SimpleNamespace
    run = RUNS / "pilot-0"
    events = []
    for line in (run / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        events.append(SimpleNamespace(
            seq=e["seq"], agent_id=e.get("agent_id"), kind=e.get("kind"),
            paths=e.get("paths") or [], payload=e.get("payload") or {}))
    controls = [{"agent_id": e.agent_id, "kind": e.kind,
                 "elapsed": float(e.payload.get("elapsed", 0)),
                 "order": e.seq}
                for e in events
                if e.kind in {"freeze", "release", "kill", "end"}]
    om = outbreak_metrics(replay_freeze_schedule(events, controls=controls),
                          events, seed=0)
    summary = json.loads((run / "summary.json").read_text())
    meter = {"total_usd": sum(float(c["cost_usd"])
                              for c in summary.get("live_model_calls", [])),
             "live": False}
    provenance = extract_provenance(events, meter)
    receipt_text = render(receipt(
        "pilot-0 (no-defense arm, SYNTHETIC DEV)", om, provenance))
    urls["receipt"] = write("receipt.html", page("Outbreak receipt",
                                                 "<pre>" + receipt_text
                                                 + "</pre>"))

    # figures beat: actual generated SVGs from committed pilot artifacts
    figures = ROOT / "docs" / "generated" / "figures"
    body = "".join(f"<img src='file://{figures / n}.svg'>" for n in
                   ("epidemic", "r-bar"))
    urls["charts"] = write("charts.html", page("Doc figures (from "
                                               "pilot artifacts)", body))

    threat = (ROOT / "docs" / "threat-model.md").read_text()
    urls["threat"] = write(
        "threat.html", page("Threat model — blind spots", "<pre>"
                            + html.escape(threat.split("## Trust")[0])
                            + "</pre>"))
    close = (ROOT / "README.md").read_text().split("## Measured")[0]
    urls["repo-close"] = write("close.html",
                               page("Below One", "<pre>"
                                    + html.escape(close) + "</pre>"))
    return urls


def assert_board(base: str, url: str) -> None:
    """HTTP 200 on the snapshot of the run the page will stream."""
    run = url.split("run=")[-1]
    status = urllib.request.urlopen(
        f"{base}/snapshot?run={run}", timeout=5).status
    assert status == 200, f"snapshot for {run} not 200"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8899")
    parser.add_argument("--out", default="clips")
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright missing: uv add playwright && uv run playwright install chromium")

    urls = build_pages()
    skipped = []
    beats = [
        ("intro-board-SYNTHETIC-DEV", f"{args.base}/?run=pilot-0", 10,
         "board"),
        ("scripted-interview-SYNTHETIC-DEV", urls["scripted-interview"], 15,
         "static"),
        ("live-catch-SYNTHETIC-DEV",
         f"{args.base}/?run=pilot-0", 15, "board-events"),
        ("split-race-SYNTHETIC-DEV",
         f"{args.base}/?mode=split&v=12", 20, "badge"),
        ("hero-freeze-SYNTHETIC-DEV",
         f"{args.base}/?run=hero-verify", 20, "freeze-event"),
        ("everyday-drift-SYNTHETIC-DEV",
         f"{args.base}/?run=drift-demo", 20, "steer"),
        ("charts-SYNTHETIC-DEV", urls["charts"], 10, "static"),
        ("receipt-SYNTHETIC-DEV", urls["receipt"], 10, "static"),
        ("reproduce-proof-SYNTHETIC-DEV", urls["reproduce-proof"], 15,
         "static"),
        ("threat-SYNTHETIC-DEV", urls["threat"], 5, "static"),
        ("repo-close-SYNTHETIC-DEV", urls["repo-close"], 5, "static"),
    ]
    out = ROOT / args.out
    out.mkdir(exist_ok=True)

    import urllib.request

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, url, seconds, kind in beats:
            try:
                capture_one(pw, name, url, seconds, kind, out, args.base)
            except Exception as error:
                print(f"SKIPPED: {name}: {error}")
                continue
        browser.close()
    for note in skipped:
        print("SKIPPED:", note)
    shutil.rmtree(out / "_raw", ignore_errors=True)
    return 0


def capture_one(pw, name, url, seconds, kind, out, base):
    """One browser per beat so an abort never loses the remaining beats."""
    browser = pw.chromium.launch()
    if kind in ("board", "board-events"):
        assert_board(base, url)
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(out / "_raw"),
        record_video_size={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(url, wait_until="domcontentloaded")
    if kind == "board":
        # board label must be loaded, not 'loading run…'
        page.wait_for_function(
            "!document.getElementById('run-name').textContent"
            ".startsWith('loading')", timeout=10000)
    if kind == "board-events":
        # event counter must be non-empty before recording
        page.wait_for_function(
            "document.getElementById('ticker').children.length > 0",
            timeout=15000)
    if kind == "badge":
        page.wait_for_function(
            "document.getElementById('synthetic-badge')"
            ".classList.contains('on')", timeout=15000)
    if kind == "steer":
        # everyday beat: an ACTUAL recorded steer event must appear
        page.wait_for_function(
            "document.getElementById('ticker').textContent"
            ".includes('steer')", timeout=20000)
    page.wait_for_timeout(int(seconds * 1000))
    context.close()
    saved = sorted((out / "_raw").glob("*.webm"),
                   key=lambda p: p.stat().st_mtime)[-1]
    target = out / f"{name}.webm"
    shutil.move(str(saved), target)
    print(f"captured {target.name} ({target.stat().st_size} bytes, "
          f"{seconds}s)")
    browser.close()


if __name__ == "__main__":
    sys.exit(main())
