#!/usr/bin/env python3
"""Render the ACTUAL same-seed paired 3-arm summary (seed 101/102 complete
runs) to presentation/figures/actual-paired-summary.png. Values come only
from experiments/derived/analysis.json runs metrics; no replay numbers."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "presentation/figures/actual-paired-summary.png"


def main() -> int:
    analysis = json.loads((ROOT / "experiments/derived/analysis.json").read_text())
    by_arm = {}
    for key, g in analysis["groups"].items():
        recs = g.get("recordings", [])
        if (g.get("run_count") == 2 and not g.get("synthetic")
                and len(recs) == 2
                and all(r.startswith("injected-kimi-") for r in recs)):
            for arm in ("no-defense", "prompt-only", "verify"):
                if f"/{arm}/" in key:
                    by_arm[arm] = g
    if set(by_arm) != {"no-defense", "prompt-only", "verify"}:
        print("no complete injected triplet groups found", file=sys.stderr)
        return 1
    arms = ("no-defense", "prompt-only", "verify")
    cells = {}
    seeds = sorted({r.rsplit("-", 1)[-1]
                    for g in by_arm.values()
                    for r in g["recordings"]})
    for arm in arms:
        m = by_arm[arm]["arms"][arm]["metrics"]
        cells[arm] = {
            "Infected (scripted P0)": m.get("infected"),
            "Peer secondary": sum(
                (m.get("secondary") or {}).values()) if isinstance(
                    m.get("secondary"), dict) else m.get("secondary"),
            "R (denominator null → not measured)": m.get("r_mean"),
            "Clean peers frozen": m.get("clean_wrongly_frozen"),
            "Held-out grader": "FAIL",
        }
    rows = ""
    for label in cells[arms[0]]:
        vals = "".join(
            f"<td>{_v(cells[a][label])}</td>" for a in arms)
        rows += f"<tr><th>{label}</th>{vals}</tr>"
    head = "".join(f"<th>{a}</th>" for a in arms)
    html = f"""<!doctype html><meta charset='utf-8'>
    <style>body{{background:#fff;color:#111;font:20px/1.5 -apple-system,Helvetica,sans-serif;padding:24px}}
    table{{border-collapse:collapse;margin-top:12px}} th,td{{border:1px solid #999;padding:10px 18px;text-align:left}}
    thead th{{background:#eee}} caption{{caption-side:top;text-align:left;font-weight:bold;padding-bottom:8px}}
    .note{{margin-top:14px;font-size:16px;color:#444}}</style>
    <h1>Actual same-seed paired 3-arm comparison — complete seeds {
        ", ".join(seeds)}</h1>
    <table><caption>Injected compromise: scripted P0 + 4 real peers per arm. Source: experiments/derived/analysis.json (sealed runs, no replay numbers).</caption>
    <thead><tr><th></th>{head}</tr></thead><tbody>{rows}</tbody></table>
    <p class='note'>R denominators are null (scripted P0 is the only infection), so R is NOT measured and no containment benefit is claimed. Clean-peer freezes in the verify arm are reported as harm.</p>"""
    path = Path(OUT).with_suffix(".html")
    path.write_text(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(path.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT), full_page=True)
        browser.close()
    print(f"{OUT} {OUT.stat().st_size}B")
    return 0


def _v(value):
    return "not measured" if value is None else value


if __name__ == "__main__":
    sys.exit(main())
