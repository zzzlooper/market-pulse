"""The dashboard in a real (headless) browser, against the app replaying a made-up morning.
See browser_kit.py for how it's isolated: scrubbed environment, throwaway config, data and
browser profile, and no network beyond this machine. Invented companies only; no people."""
import json

import pytest
from browser_kit import CHROMIUM, Browser, ReplayServer
from scenario_kit import WIRE, Day

pytestmark = pytest.mark.skipif(CHROMIUM is None, reason="needs Chromium or Chrome")

CONFIG = f'ticker_news = false\n[feeds]\n"Wire" = "{WIRE}"\n'
TOKEN = "mp-browser-test-token-7Qx2"
TOPIC = "mp-browser-topic-Zp41Kq9"


def made_up_morning(folder):
    day = Day(folder, start="04:00")
    day.filing("06:45", "424B5", "ACME")
    day.profile("ACME", "high")
    day.headline("05:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports positive topline data")
    day.premarket("07:00", ACME=(-22.0, 40), BOLT=(4.5, 12), CRUX=(38.0, 55), DYNA=(-6.2, 9))
    day.quotes("09:30", BOLT=5.0, FERN=-2.0)
    day.movers("09:31", gainers={"DYNA": 12.0, "CRUX": 41.0})
    day.halt("09:35", "BOLT", "LUDP")
    day.chart("ACME", lambda hhmm: 10.0 if hhmm < "07:00" else 7.8)
    day.earnings("04:00", BOLT=("2026-10-13", "07:00", False), FERN=("2026-10-16", "16:05", False))
    return day.build(["BOLT", "FERN"])


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    folder = tmp_path_factory.mktemp("browser")
    recording = made_up_morning(folder / "day")
    server = ReplayServer(recording, folder / "server", start="09:40", config=CONFIG)
    browser = Browser(folder)
    yield server, browser, recording, folder
    browser.stop()
    server.stop()


@pytest.fixture
def page(world):
    _, browser, _, _ = world
    p = browser.page()
    yield p
    errors = p.errors
    p.close()  # always, so a failed test doesn't leave tabs behind
    assert errors == []  # every test: no JavaScript errors or console errors


def open_dashboard(page, world, path="/"):
    page.goto(world[0].url + path)
    page.wait("document.querySelectorAll('#watchlist tr:not(.empty)').length === 2")


def test_the_dashboard_loads_and_fills_every_panel(page, world):
    open_dashboard(page, world)
    page.wait("document.querySelectorAll('#news .news-item').length >= 2")
    page.wait("document.querySelectorAll('#movers tr:not(.empty)').length === 2")
    banner = page.text("#replay-banner")
    assert banner.startswith("REPLAY of ") and "no network, no phone alerts" in banner
    assert page.text("#clock").startswith("09:4")  # the replayed time, not the real one
    assert page.text("#session") == "Market open"
    assert page.wait("document.querySelector('#health').textContent.includes('data ok')")


def test_badges_show_halts_filings_and_dilution_risk(page, world):
    open_dashboard(page, world)
    assert page.wait("document.querySelector('#watchlist a[data-sym=BOLT]')?.parentElement"
                     ".querySelector('.halt-badge')?.textContent") == "HALTED"
    acme = page.wait("[...document.querySelectorAll('#news .news-item')]"
                     ".find(li => li.textContent.includes('Acme Rocket') && li.querySelector('.risk-badge'))?.innerText",
                     timeout=20)  # the risk lookup finishes a little after the news
    assert "OFFERING FROM SHELF" in acme.upper() and "DIL" in acme


def test_earnings_badges_mark_todays_report_and_the_coming_ones(page, world):
    open_dashboard(page, world)
    badges = page.wait("document.querySelectorAll('#watchlist .er-badge').length === 2"
                       " && [...document.querySelectorAll('#watchlist tr')].filter(r => r.querySelector('.er-badge'))"
                       ".map(r => [r.querySelector('a.sym').textContent, r.querySelector('.er-badge').textContent,"
                       " r.querySelector('.er-badge').title, r.querySelector('.er-badge').classList.contains('today')])")
    assert badges == [["BOLT", "ER today", "Earnings before the open today", True],
                      ["FERN", "ER", "Earnings after the close Friday", False]]


@pytest.mark.parametrize("width", [1400, 1100])
def test_on_a_desktop_no_panel_scrolls_sideways(page, world, width):
    open_dashboard(page, world)
    page.wait("document.querySelectorAll('#watchlist .er-badge').length === 2", timeout=20)  # badges widen rows
    page.send("Emulation.setDeviceMetricsOverride", {"width": width, "height": 800, "deviceScaleFactor": 1,
                                                     "mobile": False})
    page.eval("new Promise(ok => requestAnimationFrame(() => requestAnimationFrame(ok)))")  # let it re-lay out
    assert page.eval("[...document.querySelectorAll('main > .panel')]"
                     ".filter(p => p.scrollWidth > p.clientWidth).map(p => p.id)") == []


def test_the_page_can_be_added_to_a_home_screen(page, world):
    open_dashboard(page, world)
    manifest = page.eval("fetch(document.querySelector('link[rel=manifest]').href).then(r => r.json())")
    assert manifest["display"] == "standalone" and manifest["name"] == "Market Pulse"
    for icon in [i["src"] for i in manifest["icons"]] + ["icon-180.png"]:
        size = page.eval(f"new Promise(ok => {{ const i = new Image(); i.onload = () => ok(i.naturalWidth);"
                         f" i.onerror = () => ok(0); i.src = {json.dumps(icon)}; }})")
        assert size > 0, icon
    assert page.eval("document.querySelector('meta[name=theme-color]').content") == "#0d1117"


def test_left_tabs_switch_and_keep_their_highlight_when_movers_update(page, world):
    open_dashboard(page, world)
    for tab in ("halts", "gaps", "reactions", "unusual"):
        page.click(f"[data-left-tab={tab}]")
        assert page.eval(f"!document.querySelector('#tab-{tab}').hidden")
        assert page.eval(f"document.querySelector('[data-left-tab={tab}]').classList.contains('active')")
        page.eval("renderMovers()")  # the v0.10 bug: this cleared the left panel's highlight
        assert page.eval(f"document.querySelector('[data-left-tab={tab}]').classList.contains('active')")
        assert page.eval("document.querySelectorAll('[data-left-tab].active').length") == 1


def test_opening_on_gaps_shows_the_gappers_with_their_reasons(page, world):
    open_dashboard(page, world, "/#gaps")
    rows = page.wait("document.querySelectorAll('#gaps tr:not(.empty)').length >= 4"
                     " && document.querySelector('#gaps .risk-badge')"
                     " && [...document.querySelectorAll('#gaps tr')].map(r => r.innerText)", timeout=20)
    symbols = [r.split()[0] for r in rows]
    assert symbols == ["CRUX", "ACME", "DYNA", "BOLT"]
    assert "offering from shelf" in rows[1] and "DIL" in rows[1]
    assert page.text("#gaps-note").startswith("Gaps ≥ 3%")


def test_the_ticker_drawer_opens_with_its_dilution_panel_and_closes_on_escape(page, world):
    open_dashboard(page, world)
    page.wait("!!document.querySelector('#news a.sym[data-sym=ACME]')")
    page.click("#news a.sym[data-sym=ACME]")
    page.wait("!document.querySelector('#detail').hidden")
    assert page.wait("document.querySelector('#detail-dilution .risk')?.textContent") == "high"
    assert "Shelf" in page.text("#detail-dilution")
    page.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    page.wait("document.querySelector('#detail').hidden")  # closes via history, so a moment later


def test_reactions_tab_loads_its_report_and_the_recap_preview(page, world):
    open_dashboard(page, world)
    page.click("[data-left-tab=reactions]")
    page.wait("document.querySelector('#reactions-note').textContent.includes('tracked')")
    assert page.wait("document.querySelector('#recap-preview').textContent.startsWith('Market Pulse · close recap')")


def test_reactions_can_be_downloaded_as_a_csv_file(page, world):
    open_dashboard(page, world)
    page.click("[data-left-tab=reactions]")
    page.wait("document.querySelector('#reactions-note').textContent.includes('tracked')")
    page.eval("document.querySelector('#reactions-days').value = '7';"
              " document.querySelector('#reactions-days').dispatchEvent(new Event('input'))")
    href = page.wait("document.querySelector('#reactions-csv').href.endsWith('days=7')"
                     " && document.querySelector('#reactions-csv').getAttribute('href')")
    assert href == "/api/reactions.csv?days=7"
    text = page.eval(f"fetch({json.dumps(href)}).then(r => r.text())")
    lines = text.splitlines()
    assert lines[0].startswith("time_et,symbol,type,catalyst")
    assert any(",ACME,dilution,offering from shelf," in line for line in lines[1:])


def test_setup_saves_a_secret_but_never_shows_it_back(page, world):
    open_dashboard(page, world)
    page.click("#setup-btn")
    page.wait("!document.querySelector('#setup').hidden && !!document.querySelector('#set-ntfy_topic')")
    assert page.eval("document.querySelector('#set-ntfy_topic').type") == "password"
    page.eval(f"document.querySelector('#set-ntfy_topic').value = {json.dumps(TOPIC)}")
    page.eval("document.querySelector('#set-ntfy_topic').form.requestSubmit()")
    page.wait("document.querySelector('#set-ntfy_topic')?.closest('.set-row').innerText.includes('set …')")
    html = page.eval("document.documentElement.outerHTML")
    assert TOPIC not in html and page.eval("document.querySelector('#set-ntfy_topic').value") == ""
    assert TOPIC not in world[0].get("/api/settings").text


def test_with_an_access_token_the_page_asks_for_it_first(tmp_path, world):
    _, browser, recording, _ = world
    # Top-level settings before [feeds]: after it, TOML would file the token under feeds.
    server = ReplayServer(recording, tmp_path / "locked", start="09:40", config=f'access_token = "{TOKEN}"\n' + CONFIG)
    assert server.get("/api/snapshot").status_code == 401
    page = browser.page()
    try:
        page.goto(server.url + "/")
        page.wait("document.querySelector('#login').open")
        assert page.eval("document.querySelectorAll('#watchlist tr:not(.empty)').length") == 0
        page.eval(f"document.querySelector('#login input').value = {json.dumps(TOKEN)}")
        page.eval("document.querySelector('#login form').requestSubmit()")
        page.wait("document.querySelectorAll('#watchlist tr:not(.empty)').length === 2")
        assert not page.eval("document.querySelector('#login').open")
        assert TOKEN not in page.eval("document.documentElement.outerHTML")
        # The 401s before signing in are expected; anything else is not.
        assert [e for e in page.errors if "401" not in e] == []
    finally:
        page.close()
        server.stop()


# --- on a phone -------------------------------------------------------------------------------------

# Visible elements reaching past the right edge (overflow is hidden on phones, so they'd be
# cut off rather than scrollable).
OFF_SCREEN = ("[...document.querySelectorAll('SCOPE')].filter(e => e.offsetParent && e.getBoundingClientRect().width"
              " && e.getBoundingClientRect().right > innerWidth + 1).map(e => e.tagName + '#' + e.id + '.' + e.className)")


def on_a_phone(page, world, path="/"):
    page.phone()
    page.goto(world[0].url + path)
    page.wait("document.querySelectorAll('#watchlist tr:not(.empty)').length === 2")


def test_on_a_phone_nothing_is_wider_than_the_screen_and_news_comes_right_after_the_watchlist(page, world):
    on_a_phone(page, world)
    page.wait("document.querySelectorAll('#news .news-item').length >= 2")
    assert page.eval("[document.documentElement.scrollWidth, innerWidth]") == [390, 390]  # no zoomed-out page
    # And nothing pushed off the right edge where it can't be tapped (overflow is hidden, not fixed).
    assert page.eval(OFF_SCREEN.replace("SCOPE", "header *, main *")) == []
    order = page.eval("[...document.querySelectorAll('main > .panel')]"
                      ".sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top).map(p => p.id)")
    assert order == ["watchlist-panel", "news-panel", "unusual-panel", "movers-panel", "alerts-panel"]
    # Fields at 16px, so focusing one doesn't zoom the page; buttons big enough for a thumb.
    assert page.eval("Math.min(...[...document.querySelectorAll('input:not([type=checkbox]), select')]"
                     ".filter(e => e.offsetParent).map(e => parseFloat(getComputedStyle(e).fontSize)))") >= 16
    assert page.eval("Math.min(...[...document.querySelectorAll('main button:not(.chip button)')]"
                     ".filter(e => e.offsetParent).map(e => e.getBoundingClientRect().height))") >= 36


def test_on_a_phone_a_tapped_alert_opens_the_ticker_and_back_returns_to_the_dashboard(page, world):
    on_a_phone(page, world, "/#ticker=ACME")  # what a phone alert links to (public_url/#ticker=…)
    page.wait("!document.querySelector('#detail').hidden && document.querySelector('#detail-dilution .risk')?.textContent === 'high'",
              timeout=20)
    close_visible = ("(() => { const r = document.querySelector('#detail .x').getBoundingClientRect();"
                     " return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight; })()")
    assert page.eval(close_visible)
    assert page.eval(OFF_SCREEN.replace("SCOPE", "#detail *")) == []
    page.eval("document.querySelector('#detail').scrollTop = 600")
    assert page.eval(close_visible)  # the title bar stays put while scrolling
    page.eval("history.back()")  # the phone's Back gesture
    page.wait("document.querySelector('#detail').hidden")
    assert page.eval("[location.pathname, location.hash]") == ["/", ""]
    assert page.eval("document.querySelectorAll('#watchlist tr:not(.empty)').length") == 2  # still the dashboard


@pytest.mark.parametrize("width", [360, 320])
def test_on_small_phones_nothing_is_cut_off(page, world, width):
    page.phone(width=width, height=740)
    page.goto(world[0].url + "/")
    page.wait("document.querySelectorAll('#watchlist tr:not(.empty)').length === 2"
              " && document.querySelectorAll('#news .news-item').length >= 2")
    assert page.eval(OFF_SCREEN.replace("SCOPE", "header *, main *")) == []
    page.click("#setup-btn")
    page.wait("!document.querySelector('#setup').hidden && !!document.querySelector('#set-ntfy_topic')")
    assert page.eval(OFF_SCREEN.replace("SCOPE", "#setup *")) == []


def test_on_a_phone_back_closes_setup_and_the_close_button_does_too(page, world):
    on_a_phone(page, world)
    page.click("#setup-btn")
    page.wait("!document.querySelector('#setup').hidden && !!document.querySelector('#set-ntfy_topic')")
    assert page.eval(OFF_SCREEN.replace("SCOPE", "#setup *")) == []
    page.eval("history.back()")
    page.wait("document.querySelector('#setup').hidden")
    page.click("#setup-btn")
    page.wait("!document.querySelector('#setup').hidden && !!document.querySelector('[data-setup-close]')")
    page.click("[data-setup-close]")
    page.wait("document.querySelector('#setup').hidden")
    # The × went back through history too, so no stale entry is left for the next Back.
    assert page.eval("history.state?.drawer ?? null") is None
