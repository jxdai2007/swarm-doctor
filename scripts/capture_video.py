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


DEV_BADGE = ("SYNTHETIC DEV — development recording, "
             "not live evidence")


def badge_for(source: dict | None) -> str:
    """Truthful provenance badge: synthetic default; real runs must state
    what actually happened (including an observed absence)."""
    if not source or source.get("synthetic", True):
        return DEV_BADGE
    note = source.get("note") or ("LIVE RECORDING — real measured run; "
                                  "no defense catch observed in this beat"
                                  if source.get("absent") else
                                  "LIVE RECORDING — real measured run")
    return note


def terminal(text: str, badge: str = DEV_BADGE) -> str:
    return ("<!doctype html><meta charset='utf-8'><title>terminal</title>"
            "<style>body{background:#101418;color:#e8edf2;"
            "font:16px/1.5 monospace;padding:40px}"
            "pre{white-space:pre-wrap}.badge{color:#ffd27a;"
            "border:1px solid #5a4a22;border-radius:4px;padding:2px 8px}"
            "</style><span class='badge'>" + html.escape(badge)
            + "</span><h1>terminal</h1><pre>"
            + html.escape(text) + "</pre>")


def page(title: str, body: str, badge: str = DEV_BADGE) -> str:
    return ("<!doctype html><meta charset='utf-8'><title>" + title + "</title>"
            "<style>body{background:#101418;color:#e8edf2;"
            "font:16px/1.5 monospace;padding:40px}"
            "img{max-width:1200px;display:block;margin:12px 0}"
            ".badge{color:#ffd27a;border:1px solid #5a4a22;"
            "border-radius:4px;padding:2px 8px}</style>"
            "<span class='badge'>" + html.escape(badge) + "</span><h1>"
            + title + "</h1>" + body)


def _derived_synthetic(group: str = "pilot-0") -> bool:
    """True unless the regenerated analysis says this group is real."""
    analysis = ROOT / "experiments" / "derived" / "analysis.json"
    try:
        data = json.loads(analysis.read_text())
        return bool(data["runs"][group]["config"]["synthetic"])
    except (OSError, KeyError, ValueError, TypeError):
        return True


def build_pages(pages: Path) -> tuple[dict[str, str], bool]:
    pages.mkdir(parents=True, exist_ok=True)
    live_group_badge = ("LIVE RECORDING — real measured run"
                        if not _derived_synthetic() else DEV_BADGE)

    def write(name: str, text: str) -> str:
        path = pages / f"{name}.html"
        path.write_text(text)
        return path.as_uri()

    urls = {}

    # Recorded setup spec from the real pilot archive: actual locked goal
    # spec walked through verbatim — NO synthetic task/CLI inputs. A live
    # interview was not measured; the page says so.
    pilot = ROOT / "experiments" / "committed" / "live-runs" / "pilot-0"
    spec_doc = json.loads((pilot / "spec-interviewed.json").read_text())
    one_line = json.loads((pilot / "spec-one-line.json").read_text())
    spec_body = ("<pre>Recorded setup — pilot-0 recorded goal-spec envelopes\n"
                 "spec-interviewed.json spec_hash: "
                 + html.escape(spec_doc.get("spec_hash", ""))
                 + "\nspec-one-line.json spec_hash: "
                 + html.escape(one_line.get("spec_hash", ""))
                 + "\n\n== spec-interviewed.json ==\n"
                 + html.escape(json.dumps(spec_doc, indent=2,
                                          sort_keys=True)[:4000])
                 + "\n\n== spec-one-line.json ==\n"
                 + html.escape(json.dumps(one_line, indent=2,
                                          sort_keys=True)[:2000])
                 + "</pre>")
    urls["scripted-interview"] = write(
        "interview.html",
        page("Recorded setup spec — pilot-0 (locked goal)",
             spec_body,
             badge="REAL DATA — recorded setup spec; live interview not "
                   "measured"))

    # Full comparison-only reproduction over real archives: failed proof
    # cannot become a clip. REAL DATA / OFFLINE REPRODUCTION badge.
    repro = subprocess.run(["make", "reproduce"], capture_output=True,
                           text=True, cwd=ROOT)
    if repro.returncode != 0:
        raise RuntimeError(f"make reproduce failed (exit {repro.returncode}): "
                           + (repro.stdout + repro.stderr)[-2000:])
    urls["reproduce-proof"] = write(
        "repro.html",
        terminal("$ make reproduce  [offline byte comparison over "
                 "experiments/committed/live-runs, exit 0]\n"
                 + (repro.stdout + repro.stderr).strip(),
                 badge="REAL DATA / OFFLINE REPRODUCTION — sealed real "
                       "pilot-0/pilot-1 archives"))

    # Source-bound receipt published by regeneration and checked above.
    receipt_path = ROOT / "experiments/derived/receipts/pilot-0/no-defense.txt"
    receipt_text = receipt_path.read_text()
    urls["receipt"] = write(
        "receipt.html", page("Outbreak receipt — pilot-0, no-defense",
                             "<pre>" + html.escape(receipt_text) + "</pre>",
                             badge=live_group_badge))

    # figures beat: actual generated SVGs from committed pilot artifacts
    figures = ROOT / "experiments" / "derived" / "figures"
    names = ("epidemic", "r-bar", "delay-damage")
    for name in names:
        if not (figures / f"{name}.svg").is_file():
            raise RuntimeError(f"Required figure missing: {name}")
    body = "".join(f"<img src='{(figures / (n + '.svg')).as_uri()}'>"
                   for n in names)
    urls["charts"] = write("charts.html", page("Doc figures — counterfactual "
                                               "replay of real pilot recordings "
                                               "(cached-policy arms over sealed "
                                               "pilot-0/pilot-1)", body,
                                               badge=live_group_badge))

    analysis = json.loads((ROOT / "experiments/derived/analysis.json").read_text())
    pilot_groups = [group for group in analysis["groups"].values()
                    if group["role"] == "pilot-calibration"
                    and group["agent_count"] == 3 and group["run_count"] == 2
                    and group["recordings"] == ["pilot-0", "pilot-1"]
                    and not group["synthetic"]]
    if len(pilot_groups) != 1:
        raise RuntimeError("Split comparison requires the complete real pilot-0/pilot-1 cohort")
    group = pilot_groups[0]
    panels = []
    for arm in ("no-defense", "prompt-only", "verify"):
        metrics = group["arms"][arm]["metrics"]
        rows = "".join(
            "<dt>" + label + "</dt><dd>"
            + ("not measured" if metrics[key] is None else html.escape(str(metrics[key])))
            + "</dd>" for key, label in (
                ("infected", "Mean infected agents"),
                ("r_mean", "R (secondary per infected)"),
                ("containment_rate", "Containment rate"),
                ("clean_wrongly_frozen", "Clean agents wrongly frozen"),
                ("work_completed", "Work completed"),
                ("finish_rate", "Finish rate")))
        panels.append("<section><h2>" + arm + "</h2><dl>" + rows + "</dl></section>")
    urls["split-race"] = write(
        "split.html",
        page("EXPLORATORY COUNTERFACTUAL PILOT COMPARISON",
             "<p>N=2 complete recordings; 3 agents; seeds 0/1; served model: "
             + html.escape(", ".join(group["served_models"]))
             + ". Cached-policy replay, not three live intervention arms.</p>"
             "<p>No observed infections: R and containment are not measured, "
             "not evidence of R below one or successful containment.</p>"
             "<div style='display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:32px'>"
             + "".join(panels) + "</div>",
             badge="REAL DATA — LIVE ARM COMPARISON NOT RUN (main N=0)"))

    threat = (ROOT / "docs" / "threat-model.md").read_text()
    urls["threat"] = write(
        "threat.html", page("Threat model — blind spots", "<pre>"
                            + html.escape(threat.split("## Trust")[0])
                            + "</pre>",
                            badge="DOCUMENTATION — current repo threat "
                                  "model; not a live event"))
    close = (ROOT / "README.md").read_text().split("## Measured")[0]
    urls["repo-close"] = write("close.html",
                               page("Below One", "<pre>"
                                    + html.escape(close) + "</pre>",
                                    badge="DOCUMENTATION — current repo "
                                          "README; not a live event"))
    return urls, not _derived_synthetic()


class OptionalBeatUnavailable(Exception):
    """Only missing optional source data permits skipping a beat."""


def assert_board(base: str, url: str, kind: str = "board",
                 source: dict | None = None) -> dict | None:
    """Validate the owner's real snapshot and /events stream before a beat.

    Returns the source entry updated with truthful absence info for
    required-event beats over real recordings ('no freeze/steer observed'
    stays capturable when counters/event stream prove the absence).
    """
    source = dict(source) if source else {"synthetic": True}
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
            # Real recording: refuse unless this beat explicitly declares a
            # real source with provenance; never mix labels.
            if source.get("synthetic", True):
                raise RuntimeError(
                    f"{name}: snapshot is a real recording but beat is not "
                    "declared real; refusing synthetic label on live source")
            if not snapshot.get("agents") or not snapshot.get("counters"):
                raise RuntimeError(f"{name}: real snapshot missing provenance state")
        else:
            if not source.get("synthetic", True):
                raise RuntimeError(
                    f"{name}: beat declared real but snapshot is synthetic")
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
                if not source.get("synthetic", True) and source.get("allow_absent"):
                    counters = {row.get("label"): row.get("value")
                                for row in snapshot.get("counters", [])}
                    if kind == "freeze-event" and counters.get("Frozen agents") != 0:
                        raise RuntimeError(
                            f"{name}: zero freeze events but counter says "
                            f"{counters.get('Frozen agents')!r}; refusing "
                            "unproven absence claim")
                    source["absent"] = required
                else:
                    raise RuntimeError(f"{name}: no actual {required} event")
    return source


DEFAULT_SOURCES = {
    "board": {"run": "pilot-0", "synthetic": True},
    "catch": {"run": "pilot-0", "synthetic": True},
    "split": {"synthetic": True},
    "charts": {"synthetic": True},
    "receipt": {"synthetic": True},
    "freeze": {"run": "hero-verify", "synthetic": True},
    "steer": {"run": "drift-demo", "synthetic": True},
}


def _clip_name(base_name: str, source: dict) -> str:
    if source.get("kind_label"):
        return base_name + "-REAL-" + source["kind_label"]
    if source.get("unmeasured"):
        return base_name + "-REAL-unmeasured"
    if source.get("synthetic", True):
        return base_name + "-SYNTHETIC-DEV"
    suffix = ""
    if source.get("absent"):
        suffix = "-no-" + source["absent"] + "-observed"
    return base_name + "-REAL" + suffix


def storyboard(base: str, urls: dict[str, str],
               sources: dict | None = None,
               live_group_real: bool = False) -> list[tuple]:
    """Beat sources come from clips/sources.json when present (real run IDs,
    synthetic flags, allow_absent for truthful no-event beats); defaults are
    the committed synthetic-dev captures."""
    merged = {key: dict(value) for key, value in DEFAULT_SOURCES.items()}
    for key, value in (sources or {}).items():
        if key not in merged:
            raise ValueError(f"unknown source beat: {key}")
        merged[key].update(value)
    base_url = base.rstrip("/")
    board, catch = merged["board"], merged["catch"]
    split = merged["split"]
    charts = dict(merged["charts"])
    if live_group_real:
        charts["synthetic"] = False
        charts.setdefault("mode", "counterfactual-replay")
    receipt = dict(merged["receipt"])
    if live_group_real:
        receipt["synthetic"] = False
        receipt.setdefault("mode", "actual")
    freeze = assert_board(base_url, f"{base_url}/?run={quote(merged['freeze']['run'])}",
                          "freeze-event", merged["freeze"])
    steer = assert_board(base_url, f"{base_url}/?run={quote(merged['steer']['run'])}",
                         "steer", merged["steer"])
    static_sources = {
        "scripted-interview": {"synthetic": False, "mode": "recorded-setup-spec",
                               "run": "pilot-0",
                               "kind_label": "recorded-setup",
                               "note": "actual locked pilot-0 spec walkthrough; live interview not measured"},
        "reproduce-proof": {"synthetic": False, "mode": "offline-reproduction",
                            "runs": ["pilot-0", "pilot-1"],
                            "kind_label": "offline-reproduction",
                            "note": "REAL DATA / OFFLINE REPRODUCTION over sealed real archives"},
        "threat": {"synthetic": False, "mode": "documentation",
                   "kind_label": "documentation",
                   "note": "current repo docs/threat-model.md; not a live event"},
        "repo-close": {"synthetic": False, "mode": "documentation",
                       "kind_label": "documentation",
                       "note": "current repo README; not a live event"},
    }
    return [
        (_clip_name("intro-board", board), f"{base}/?run={quote(board['run'])}", 10, "board", board),
        (_clip_name("scripted-interview", static_sources["scripted-interview"]), urls["scripted-interview"], 15, "static", static_sources["scripted-interview"]),
        (_clip_name("live-catch", catch), f"{base}/?run={quote(catch['run'])}", 15, "board-events", catch),
        (_clip_name("split-race", split), urls["split-race"], 20, "static", split),
        (_clip_name("hero-freeze", freeze), f"{base}/?run={quote(merged['freeze']['run'])}", 20, "freeze-event", freeze),
        (_clip_name("everyday-drift", steer), f"{base}/?run={quote(merged['steer']['run'])}", 20, "steer", steer),
        (_clip_name("charts", charts), urls["charts"], 10, "figures", charts),
        (_clip_name("receipt", receipt), urls["receipt"], 10, "static", receipt),
        (_clip_name("reproduce-proof", static_sources["reproduce-proof"]), urls["reproduce-proof"], 15, "static", static_sources["reproduce-proof"]),
        (_clip_name("threat", static_sources["threat"]), urls["threat"], 5, "static", static_sources["threat"]),
        (_clip_name("repo-close", static_sources["repo-close"]), urls["repo-close"], 5, "static", static_sources["repo-close"]),
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8899")
    parser.add_argument("--out", default="clips/current")
    parser.add_argument("--sources",
                        help="JSON file mapping board/catch/freeze/steer "
                             "beats to run IDs with synthetic flags and "
                             "allow_absent for real no-event beats")
    args = parser.parse_args(argv)
    sources = None
    if args.sources:
        sources = json.loads(Path(args.sources).read_text())
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    inventory = {"invocation": uuid4().hex,
                 "started": datetime.now(timezone.utc).isoformat(),
                 "sources": sources, "all_synthetic": None,
                 "produced": [], "skipped": [],
                 "failures": [], "status": "running"}

    def save():
        (out / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")

    save()  # Immediately invalidate any previous invocation's inventory.
    try:
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=out) as temporary:
            stage = Path(temporary)
            urls, live_group_real = build_pages(stage / "pages")
            for name, url, seconds, kind, source in storyboard(
                    args.base, urls, sources, live_group_real):
                try:
                    clip = capture_one(name, url, seconds, kind, stage, args.base, source)
                    if clip.parent != stage or not clip.is_file() or clip.stat().st_size == 0:
                        raise RuntimeError(f"{name}: no current invocation video")
                    target = out / clip.name
                    shutil.move(str(clip), target)
                    inventory["produced"].append(
                        {"beat": name, "file": target.name, "seconds": seconds,
                         "bytes": target.stat().st_size,
                         "source_real": not (source or {}).get("synthetic", True),
                         "mode": ((source or {}).get("mode")
                                  or ("unmeasured" if (source or {}).get("unmeasured")
                                      else "actual-absence" if (source or {}).get("absent")
                                      else "actual" if not (source or {}).get("synthetic", True)
                                      else "static")),
                         "run": (source or {}).get("run"),
                         "runs": (source or {}).get("runs"),
                         "note": (source or {}).get("note")})
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
    inventory["all_synthetic"] = all(
        not row["source_real"] for row in inventory["produced"])
    save()
    return int(bool(inventory["failures"]))


def capture_one(name, url, seconds, kind, out, base, source=None):
    """One browser per beat so an abort never loses the remaining beats.
    context.close() flushes the video before the file move; browser.close()
    always runs in finally."""
    if kind in {"board", "board-events", "split", "freeze-event", "steer"} \
            and not (source or {}).get("unmeasured"):
        assert_board(base, url, kind, source)
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
                if (source or {}).get("unmeasured"):
                    # Real unmeasured beat: the three-arm comparison was not
                    # run (main study N=0); the panel MUST show its
                    # unavailable markers, never synthetic fixture panels.
                    page.wait_for_function(
                        "document.getElementById('split') !== null"
                        " && document.getElementById('split').textContent.includes('unavailable')",
                        timeout=15000)
                else:
                    page.wait_for_function(
                        "document.querySelectorAll('#split .panel').length === 3"
                        " && !document.getElementById('split').textContent.includes('unavailable')"
                        " && document.getElementById('synthetic-badge').classList.contains('on')",
                        timeout=15000)
            if kind == "freeze-event" and not (source or {}).get("absent"):
                page.wait_for_function(
                    "document.querySelector('#ticker .freeze') !== null", timeout=30000)
            if kind == "figures":
                page.wait_for_function(
                    "document.images.length === 3 && Array.from(document.images)"
                    ".every(img => img.complete && img.naturalWidth > 0)", timeout=15000)
            if kind == "steer" and not (source or {}).get("absent"):
                # everyday beat: an ACTUAL recorded steer event must appear
                page.wait_for_function(
                    "document.getElementById('ticker').textContent"
                    ".includes('steer')", timeout=20000)
            video = page.video
            if video is None:
                raise RuntimeError(f"{name}: browser produced no video")
            if kind in {"static", "figures"}:
                page.evaluate("""duration => {
                    const overflow = document.scrollingElement.scrollHeight - innerHeight;
                    if (overflow <= 0) return;
                    const start = performance.now();
                    function pan(now) {
                        const progress = Math.min(1, Math.max(0,
                            (now - start - duration * 0.15) / (duration * 0.7)));
                        window.scrollTo(0, overflow * progress);
                        if (now - start < duration) requestAnimationFrame(pan);
                    }
                    requestAnimationFrame(pan);
                }""", seconds * 1000)
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
