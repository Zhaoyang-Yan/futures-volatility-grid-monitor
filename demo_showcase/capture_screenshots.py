"""Capture fixed-size portfolio screenshots from the local synthetic showcase."""

from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "screenshots"
URL = "http://127.0.0.1:8765/"


def main():
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.goto(URL, wait_until="networkidle")
        page.screenshot(path=str(OUT / "01_market_overview.png"), full_page=False)
        page.get_by_text("Contract Monitor", exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / "02_contract_grid_monitor.png"), full_page=False)
        page.get_by_text("Implied Volatility Cone", exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(OUT / "03_volatility_cone.png"), full_page=False)
        browser.close()
    print("Captured 3 synthetic showcase screenshots at 1920x1080")


if __name__ == "__main__":
    main()
