"""Снимает build/screens/*.html в docs/screens/*.png (Playwright, Chromium).

python tools/shoot.py   # нужен pip install playwright && playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "build" / "screens"
DST = ROOT / "docs" / "screens"


def main() -> None:
    DST.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 800}, device_scale_factor=2)
        for html in sorted(SRC.glob("*.html")):
            page.goto(html.as_uri())
            page.wait_for_load_state("networkidle")
            target = page.locator(".sheet").first
            target.screenshot(path=str(DST / f"{html.stem}.png"))
            print(DST / f"{html.stem}.png")
        browser.close()


if __name__ == "__main__":
    main()
