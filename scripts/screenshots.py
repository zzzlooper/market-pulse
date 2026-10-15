"""Screenshots for the README: the real dashboard in headless Chromium, replaying the browser
tests' made-up morning (tests/test_browser.py). Invented companies only, a throwaway config,
data folder and browser profile, and no network beyond this machine (see tests/browser_kit.py).

    python scripts/screenshots.py          # writes docs/screenshots/*.png
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
# As in tests/conftest.py, set before anything imports the app: never your config.toml or data folder.
os.environ["MARKET_PULSE_CONFIG"] = os.path.join(tempfile.mkdtemp(prefix="mp-shots-"), "config.toml")
os.environ["MARKET_PULSE_DATA"] = tempfile.mkdtemp(prefix="mp-shots-data-")

from browser_kit import Browser, ReplayServer  # noqa: E402
from test_browser import CONFIG, made_up_morning  # noqa: E402

OUT = ROOT / "docs" / "screenshots"


def settle(page) -> None:
    page.eval("new Promise(ok => setTimeout(ok, 1500))")  # let late lookups (risk, badges) land


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        server = ReplayServer(made_up_morning(folder / "day"), folder / "server", start="09:40", config=CONFIG)
        browser = Browser(folder)
        try:
            page = browser.page()
            page.send("Emulation.setDeviceMetricsOverride",
                      {"width": 1400, "height": 860, "deviceScaleFactor": 1, "mobile": False})
            # Market time, not this machine's: times match the ET clock and don't reveal where you are.
            page.send("Emulation.setTimezoneOverride", {"timezoneId": "America/New_York"})
            page.goto(server.url + "/")
            page.wait("document.querySelectorAll('#watchlist .er-badge').length === 2", timeout=20)
            page.wait("document.querySelectorAll('#news .news-item').length >= 2")
            page.wait("[...document.querySelectorAll('#news .news-item')].some(li => li.querySelector('.risk-badge'))",
                      timeout=20)
            settle(page)
            page.screenshot(OUT / "dashboard.png")

            page.click("#news a.sym[data-sym=ACME]")
            page.wait("document.querySelectorAll('#detail-levels li').length === 4 && !!detail.geom", timeout=20)
            settle(page)
            page.screenshot(OUT / "ticker.png")

            page.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
            page.wait("document.querySelector('#detail').hidden")
            page.wait("state.gaps.length >= 4", timeout=20)
            page.click("#plan-btn")
            page.wait("document.querySelectorAll('.plan-card').length >= 4")
            settle(page)
            page.screenshot(OUT / "plan.png")
            page.close()
        finally:
            browser.stop()
            server.stop()
    for png in sorted(OUT.glob("*.png")):
        print(png.relative_to(ROOT))


if __name__ == "__main__":
    main()
