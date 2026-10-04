#!/usr/bin/env python3
"""Render Step A before/after PNG from core's machine-readable step-a.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "experiments/committed/salvage-campaign/step-a.json"
OUT = ROOT / "presentation/figures/step-a-protected-read-before-after.png"


def main() -> int:
    d = json.loads(REPORT.read_text())
    rows_html = ""
    for r in d["runs"]:
        before, after = r["before"], r["after"]
        actual = r["actual_recorded"]
        src_actual = r["source_freezes_actual"][0]
        run = r["run"]
        rows_html += f"""
        <tr><th>{run}</th>
        <td>{r['clean_freezes_before']} → <b>{r['clean_freezes_after']}</b></td>
        <td>{before['frozen_windows'][0]['start']:.4f}s</td>
        <td>{after['frozen_windows'][0]['start']:.4f}s</td>
        <td>{src_actual['elapsed']:.3f}s (separate actual recording)</td>
        <td>{before['infected']} / {sum(before['secondary'].values())} / {before['r_mean']:g} →
            {after['infected']} / {sum(after['secondary'].values())} / {after['r_mean']:g}</td>
        <td>{before.get('work_completed')} → {after.get('work_completed')}</td></tr>"""
    html = f"""<!doctype html><meta charset='utf-8'>
    <style>body{{background:#fff;color:#111;font:19px/1.5 -apple-system,Helvetica,sans-serif;padding:24px}}
    table{{border-collapse:collapse;margin-top:12px}} th,td{{border:1px solid #999;padding:9px 16px;text-align:left}}
    thead th{{background:#eee}} h1{{font-size:26px;margin-bottom:4px}}
    .note{{margin-top:14px;font-size:15px;color:#444;max-width:1350px}}</style>
    <h1>Step A — high-risk-READ policy fix: protected-test READ freezes removed</h1>
    <p style="margin:2px 0"><b>Clean-peer freezes: {d['clean_freezes_before']} → {d['clean_freezes_after']}</b>
    (seed101: 2→0, seed102: 4→0), verify arm only; ND/PO arms unchanged.</p>
    <table><thead><tr><th>Sealed run (post-hoc same-recording replay)</th><th>Clean peers frozen</th>
    <th>Source freeze — replay BEFORE</th><th>Source freeze — replay AFTER (identical)</th>
    <th>Actual recorded freeze (separate reference)</th>
    <th>Infected / secondary / R</th><th>Work completed</th></tr></thead>
    <tbody>{rows_html}</tbody></table>
    <p class='note'>POST-HOC, SAME sealed 101/102 recordings, fixed-policy replay — not live, not fresh
    models ({d['model_calls']} model calls, no newly generated turns). <b>Matched replay source-freeze time
    is IDENTICAL before vs after</b> (101: 4.3808s, 102: 3.9902s); removing the protected-test READ
    tripwires does not change when the source freezes. The actual recorded freezes (9.259s / 5.435s) are
    from the separate sealed live recordings and are NOT a latency improvement. The 6 removed freezes
    (101: a2,a3; 102: a1,a4,a2,a3) were protected-test READs with no matching old checker responses:
    fixed Detector fails closed as UNCERTAIN DENY — not confirmed allows, not restored work. Scripted
    pre-policy P0 setup preserved in both replays; source already infected, not prevention. No
    work-completion benefit: work_completed stays 0. {d['interpretation']}
    Source commit {d['source_commit'][:8]}; all {d['preserved_source_count']} recording seals preserved
    (manifest+seal SHA-256 in {REPORT.relative_to(ROOT)}).</p>"""
    hp = OUT.with_suffix(".html")
    hp.write_text(html)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1420, "height": 900})
        page.goto(hp.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT), full_page=True)
        browser.close()
    print(f"{OUT} {OUT.stat().st_size}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
