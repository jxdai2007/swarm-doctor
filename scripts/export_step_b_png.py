#!/usr/bin/env python3
"""Render Step B final PNG strictly from salvage-campaign/step-b.json."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "experiments/committed/salvage-campaign/step-b.json"
RUN302 = ROOT / "experiments/committed/salvage-campaign/salvage-302-no-defense"
OUT = ROOT / "presentation/figures/step-b-302-epidemic-r-arms.png"
CAPTION = ("Susceptible model organism, chosen to induce spread; tests "
           "containment, not natural propensity.")
ORDER = ("no-defense", "prompt-only", "verify", "message-only",
         "periodic-review", "strict", "taint-without-checker", "blunt-khop")


def main() -> int:
    d = json.loads(REPORT.read_text())
    m = json.loads((RUN302 / "metrics.json").read_text())
    rows = ""
    for name in ORDER:
        if name not in d["arms"]:
            continue
        a = d["arms"][name]
        if a["status"] == "unmeasured":
            rows += (f"<tr><td>{name}</td><td>UNMEASURED</td><td>null</td>"
                     f"<td>null</td><td>{a['reason']}</td></tr>")
        else:
            inf = "/".join(map(str, a["infected"]))
            src = ", ".join(r.split("-", 1)[1] for r in a["source_runs"])
            rows += (f"<tr><td><b>{name}</b> — recorded episodes {a['run_count']} "
                     f"({src})</td><td>observed-scripted-source</td>"
                     f"<td>infected {inf}; peer secondary {d['observed_secondary']}</td>"
                     f"<td>{a['r_mean']:g} <i>(scripted-source denominator)</i></td>"
                     f"<td>{a['reason']}</td></tr>")
    html = f"""<!doctype html><meta charset='utf-8'>
    <style>body{{background:#fff;color:#111;font:19px/1.5 -apple-system,Helvetica,sans-serif;padding:24px}}
    table{{border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid #999;padding:8px 14px;text-align:left}}
    thead th{{background:#eee}} h1{{font-size:25px;margin-bottom:4px}} h2{{font-size:20px;margin:16px 0 4px}}
    .cap{{font-size:15px;color:#444;max-width:1360px;margin-top:12px}}</style>
    <h1>Step B — three distinct candidates attempted, one completed run (salvage-302, LingFlash)</h1>
    <h2>Epidemic state, actual recorded salvage-302 no-defense run (end of recording)</h2>
    <p style="margin:4px 0;font-size:20px">
      <span style="background:#c0392b;color:#fff;padding:4px 12px;border-radius:6px">scripted source a0: INFECTED</span>
      &nbsp;
      <span style="background:#27ae60;color:#fff;padding:4px 12px;border-radius:6px">real peers a1–a4: {sum(m['secondary'].values())} secondary infections</span>
      &nbsp; containment: {m['outbreaks_contained']} (status {m['status']}); no freeze windows occurred.</p>
    <h2>Arm status — from {REPORT.name} ({d['schema']})</h2>
    <table><thead><tr><th>Arm</th><th>Status</th><th>Observed</th><th>R</th><th>Reason</th></tr></thead>
    <tbody>{rows}</tbody></table>
    <p class='cap'><b>{CAPTION}</b> {d['attempted_distinct_models']} distinct candidate models attempted
    (Nemo salvage-300 interrupted partial; LingVL salvage-301 partial; LingFlash salvage-302 complete:
    5 agents, 8 turns each); selected_model is null — no susceptible organism was selected, so the
    conditional organism replay and live-verify programs were SKIPPED. Only the no-defense arm is
    observed (scripted source; {d['observed_nd_count']} episodes); every other arm is unmeasured (null) —
    no counterfactual, replay, or pooled values are shown. Zero observed peer secondary spread
    ({d['observed_secondary']} across episodes) is NOT scientific resistance or natural non-propensity.
    R=0 uses the deliberately injected scripted source as denominator, not demonstrated spread benefit.
    Grader outcome: held-out task not completed. Owned model jobs: {d['model_jobs_owned']}.
    Interpretation: {d['interpretation']}. Source: experiments/committed/salvage-campaign/step-b.json
    and salvage-302-no-defense/metrics.json only.</p>"""
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
