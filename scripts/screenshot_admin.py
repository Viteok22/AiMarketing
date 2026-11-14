from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "screens"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("http://127.0.0.1:8000/login", wait_until="networkidle")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        page.screenshot(path=str(OUT / "light-dashboard.png"), full_page=True)

        page.goto("http://127.0.0.1:8000/companies/c-salavatsteklo", wait_until="networkidle")
        page.screenshot(path=str(OUT / "light-company.png"), full_page=True)

        page.goto("http://127.0.0.1:8000/analytics", wait_until="networkidle")
        page.screenshot(path=str(OUT / "light-analytics.png"), full_page=True)

        page.goto("http://127.0.0.1:8000/trends", wait_until="networkidle")
        page.screenshot(path=str(OUT / "light-trends.png"), full_page=True)
        browser.close()
    print(OUT / "light-dashboard.png")
    print(OUT / "light-company.png")
    print(OUT / "light-analytics.png")
    print(OUT / "light-trends.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
