#!/usr/bin/env python3
"""Export slide-ready PNGs from generated figure SVGs (playwright render)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIG = ROOT / "experiments/derived/figures"
OUT = ROOT / "presentation/figures"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    from playwright.sync_api import sync_playwright
    svgs = sorted(FIG.glob("*.svg"))
    if not svgs:
        print("no figures found", file=sys.stderr)
        return 1
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        for svg in svgs:
            page.goto(svg.as_uri(), wait_until="networkidle")
            page.wait_for_timeout(300)
            el = page.query_selector("svg")
            box = (el or page.query_selector("body")).bounding_box()
            out = OUT / (svg.stem + ".png")
            page.screenshot(path=str(out), clip=box or None)
            print(f"{out.name} {out.stat().st_size}B")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
