#!/usr/bin/env python3
"""U13 beat capture: records every storyboard beat from REAL surfaces —
the engine board serving canonical pilot artifacts, actual CLI/make output
rendered as terminal pages, actual generated figures and receipts.

Integrity rules:
- Required beat or comparison-only reproduce failure exits nonzero.
- Inventory includes only videos produced by this invocation, never stale clips.
- Split, hero-freeze and drift may skip only when source snapshots are absent
  (HTTP 404); present sources must capture successfully.
- All displayed evidence remains explicitly synthetic-development.
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
import urllib.error
from urllib.parse import parse_qs, quote, urlparse
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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


def build_pages(pages: Path) -> dict[str, str]:
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
    shutil.rmtree(work)
    if cli.returncode != 0:
        raise RuntimeError(f"interview CLI failed (exit {cli.returncode}): "
                           f"{cli.stderr.strip()}")
    urls["scripted-interview"] = write(
        "interview.html",
        terminal("$ uv run python -m belowone.cli interview --scripted … "
                 "(model-mode synthetic, exit 0)\n"
                 + (cli.stdout + cli.stderr).strip()))

    # Full comparison-only reproduction: failed proof cannot become a clip.
    repro = subprocess.run(["make", "reproduce"], capture_output=True,
                           text=True, cwd=ROOT)
    if repro.returncode != 0:
        raise RuntimeError(f"make reproduce failed (exit {repro.returncode}): "
                           + (repro.stdout + repro.stderr)[-2000:])
    urls["reproduce-proof"] = write(
        "repro.html",
        terminal("$ make reproduce  [full offline byte comparison, exit 0]\n"
                 + (repro.stdout + repro.stderr).strip()))

    # Source-bound receipt published by regeneration and checked above.
    receipt_path = ROOT / "experiments/derived/receipts/pilot-0/no-defense.txt"
    receipt_text = receipt_path.read_text()
    urls["receipt"] = write(
        "receipt.html", page("Outbreak receipt — pilot-0, no-defense",
                             "<pre>" + html.escape(receipt_text) + "</pre>"))

    # figures beat: actual generated SVGs from committed pilot artifacts
    figures = ROOT / "experiments" / "derived" / "figures"
    names = ("epidemic", "r-bar", "delay-damage")
    for name in names:
        if not (figures / f"{name}.svg").is_file():
            raise RuntimeError(f"Required figure missing: {name}")
    body = "".join(f"<img src='{(figures / (n + '.svg')).as_uri()}'>"
                   for n in names)
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


class OptionalBeatUnavailable(Exception):
    """Only missing optional source data permits skipping a beat."""


def assert_board(base: str, url: str, kind: str = "board") -> None:
    """Validate the owner's real snapshot and /events stream before a beat."""
    run = parse_qs(urlparse(url).query).get("run", ["live"])[0]
    runs = ("derived-no-defense", "derived-prompt-only", "derived-verify") \
        if kind == "split" else (run,)
    for name in runs:
        try:
            with urllib.request.urlopen(
                    f"{base.rstrip('/')}/snapshot?run={quote(name)}", timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError(f"snapshot for {name}: HTTP {response.status}")
                snapshot = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404 and kind in {"split", "freeze-event", "steer"}:
                raise OptionalBeatUnavailable(f"source snapshot absent: {name}") from error
            raise
        if not snapshot.get("synthetic"):
            raise RuntimeError(f"{name}: refusing synthetic label on non-synthetic source")
        if not snapshot.get("agents") or not snapshot.get("counters"):
            raise RuntimeError(f"{name}: snapshot missing board state/counters")
        if kind not in {"board-events", "freeze-event", "steer"}:
            continue
        required = {"freeze-event": "freeze", "steer": "steer"}.get(kind)
        history = []
        request = urllib.request.Request(
            f"{base.rstrip('/')}/events?run={quote(name)}",
            headers={"Accept": "text/event-stream"})
        try:
            with urllib.request.urlopen(request, timeout=30) as stream:
                for block in stream:
                    for line in block.splitlines():
                        if line.startswith(b"data: ") and line[6:] not in (b"[done]", b""):
                            history.append(json.loads(line[6:]))
                    if b"[done]" in block:
                        break
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code == 404 \
                    and kind in {"freeze-event", "steer"}:
                raise OptionalBeatUnavailable(f"source events absent: {name}") from error
            raise RuntimeError(f"historical events for {name}: {error}") from error
        else:
            if not history:
                raise RuntimeError(f"{name}: empty recorded event stream")
            if required and not any(event.get("kind") == required for event in history):
                raise RuntimeError(f"{name}: no actual {required} event")


def storyboard(base: str, urls: dict[str, str]) -> list[tuple]:
    base = base.rstrip("/")
    return [
        ("intro-board-SYNTHETIC-DEV", f"{base}/?run=pilot-0", 10, "board"),
        ("scripted-interview-SYNTHETIC-DEV", urls["scripted-interview"], 15, "static"),
        ("live-catch-SYNTHETIC-DEV", f"{base}/?run=pilot-0", 15, "board-events"),
        ("split-race-SYNTHETIC-DEV", f"{base}/?mode=split", 20, "split"),
        ("hero-freeze-SYNTHETIC-DEV", f"{base}/?run=hero-verify", 20, "freeze-event"),
        ("everyday-drift-SYNTHETIC-DEV", f"{base}/?run=drift-demo", 20, "steer"),
        ("charts-SYNTHETIC-DEV", urls["charts"], 10, "figures"),
        ("receipt-SYNTHETIC-DEV", urls["receipt"], 10, "static"),
        ("reproduce-proof-SYNTHETIC-DEV", urls["reproduce-proof"], 15, "static"),
        ("threat-SYNTHETIC-DEV", urls["threat"], 5, "static"),
        ("repo-close-SYNTHETIC-DEV", urls["repo-close"], 5, "static"),
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8899")
    parser.add_argument("--out", default="clips/current")
    args = parser.parse_args(argv)
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    inventory = {"invocation": uuid4().hex,
                 "started": datetime.now(timezone.utc).isoformat(),
                 "synthetic": True, "produced": [], "skipped": [],
                 "failures": [], "status": "running"}

    def save():
        (out / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")

    save()  # Immediately invalidate any previous invocation's inventory.
    try:
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=out) as temporary:
            stage = Path(temporary)
            urls = build_pages(stage / "pages")
            for name, url, seconds, kind in storyboard(args.base, urls):
                try:
                    clip = capture_one(name, url, seconds, kind, stage, args.base)
                    if clip.parent != stage or not clip.is_file() or clip.stat().st_size == 0:
                        raise RuntimeError(f"{name}: no current invocation video")
                    target = out / clip.name
                    shutil.move(str(clip), target)
                    inventory["produced"].append(
                        {"beat": name, "file": target.name, "seconds": seconds,
                         "bytes": target.stat().st_size})
                except OptionalBeatUnavailable as error:
                    if kind not in {"split", "freeze-event", "steer"}:
                        raise
                    inventory["skipped"].append({"beat": name, "reason": str(error)})
                    print(f"SKIPPED OPTIONAL: {name}: {error}")
                except Exception as error:
                    inventory["failures"].append({"beat": name, "reason": str(error)})
                    print(f"FAILED REQUIRED: {name}: {error}")
                save()
    except Exception as error:
        inventory["failures"].append({"beat": "preparation", "reason": str(error)})
        print(f"FAILED REQUIRED: preparation: {error}")
    inventory["status"] = "failed" if inventory["failures"] else "complete"
    save()
    return int(bool(inventory["failures"]))


def capture_one(name, url, seconds, kind, out, base):
    """One browser per beat so an abort never loses the remaining beats.
    context.close() flushes the video before the file move; browser.close()
    always runs in finally."""
    if kind in {"board", "board-events", "split", "freeze-event", "steer"}:
        assert_board(base, url, kind)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(out / "_raw"),
                record_video_size={"width": 1440, "height": 900})
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded")
            if response is not None and not response.ok:
                raise RuntimeError(f"{name}: page HTTP {response.status}")
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
            if kind == "split":
                page.wait_for_function(
                    "document.querySelectorAll('#split .panel').length === 3"
                    " && !document.getElementById('split').textContent.includes('unavailable')"
                    " && document.getElementById('synthetic-badge').classList.contains('on')",
                    timeout=15000)
            if kind == "freeze-event":
                page.wait_for_function(
                    "document.querySelector('#ticker .freeze') !== null", timeout=30000)
            if kind == "figures":
                page.wait_for_function(
                    "document.images.length === 3 && Array.from(document.images)"
                    ".every(img => img.complete && img.naturalWidth > 0)", timeout=15000)
            if kind == "steer":
                # everyday beat: an ACTUAL recorded steer event must appear
                page.wait_for_function(
                    "document.getElementById('ticker').textContent"
                    ".includes('steer')", timeout=20000)
            video = page.video
            if video is None:
                raise RuntimeError(f"{name}: browser produced no video")
            try:
                page.wait_for_timeout(int(seconds * 1000))
            finally:
                context.close()  # flushes the video before the file move
            saved = Path(video.path())
            target = out / f"{name}.webm"
            shutil.move(str(saved), target)
            print(f"captured {target.name} ({target.stat().st_size} bytes, "
                  f"{seconds}s)")
            return target
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
