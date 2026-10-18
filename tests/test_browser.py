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
WARRANTS = """<p>The public offering price is $9.00 per share.</p>
<p>Up to 1,000,000 Shares of Common Stock underlying the Series A Warrants</p>
<p>Each Series A Warrant has an exercise price of $7.50 per share.</p>
<p>Excludes 3,000,000 shares of common stock issuable upon the exercise of outstanding warrants,
having a weighted average exercise price of $12.50 per share.</p>"""


def made_up_morning(folder):
    day = Day(folder, start="04:00")
    day.filing("06:45", "424B5", "ACME")
    day.profile("ACME", "high", offering_text=WARRANTS,  # trades at 7.80: the $7.50 warrants are in the money
                split_text="<p>a 1-for-10 reverse stock split, effective at market open on October 15, 2026</p>")
    day.headline("05:30", "Crux Biotherapeutics Inc (NASDAQ: CRUX) reports positive topline data")
    day.premarket("07:00", ACME=(-22.0, 40), BOLT=(4.5, 12), CRUX=(38.0, 55), DYNA=(-6.2, 9))
    day.quotes("09:30", BOLT=5.0, FERN=-2.0)
    day.movers("09:31", gainers={"DYNA": 12.0, "CRUX": 41.0})
    day.halt("09:35", "BOLT", "LUDP")
    day.halt("09:38", "DYNA", "LUDP", resumes="09:55")  # a mover, resuming while the tests run
    day.chart("ACME", lambda hhmm: 10.0 if hhmm < "07:00" else 7.8)
    day.chart("BOLT", lambda hhmm: 10.0 if hhmm < "09:30" else 10.5)
    day.profile("BOLT", "low")
    day.profile("DYNA", "low")
    day.chart("DYNA", lambda hhmm: 11.2)
    day.earnings("04:00", BOLT=("2026-10-13", "07:00", False), FERN=("2026-10-16", "16:05", False))
    day.daily("04:00", ACME=[("2026-10-09", 9.6, 10.0), ("2026-10-12", 8.5, 9.0)],  # fell 15% yesterday: SSR today
              CRUX=[("2026-10-09", 9.0, 8.0), ("2026-10-12", 8.0, 10.0)])           # up 25%... not day 2
    day.daily("04:00", DYNA=[("2026-10-09", 6.0, 6.0), ("2026-10-12", 6.0, 9.0)])    # up 50%: day 2
    day.floats("04:00", BOLT=800_000, FERN=50_000_000,  # BOLT has traded 1M shares: its float, 1.3 times
               ACME=None, CRUX=None, DYNA=None,
               industry={"ACME": "Aerospace & Defense", "BOLT": "Electrical Equipment & Parts", "FERN": "Packaged Foods",
                         "CRUX": "Biotechnology", "DYNA": "Biotechnology"})
    day.industry("04:00", "Aerospace & Defense", ACME=-22.0)
    day.industry("04:00", "Electrical Equipment & Parts", BOLT=5.0)
    day.industry("04:00", "Biotechnology", CRUX=41.0, DYNA=12.0)  # DYNA moves with CRUX
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
    war = page.wait("document.querySelector('#gaps a.sym[data-sym=ACME]').parentElement.querySelector('.war-badge')?.title")
    assert war.startswith("Warrants in the money: Series A warrants at $7.50 for 1.00M shares (424B5 of Aug")
    assert "12.50" not in war  # the outstanding ones are still out of the money
    assert page.eval("document.querySelectorAll('#gaps .war-badge').length") == 1
    ssr = page.wait("document.querySelector('#gaps a.sym[data-sym=ACME]').parentElement.querySelector('.ssr-badge')?.title")
    assert ssr.startswith("Short sale restriction all day today, carried over from the 10% drop on Oct 12")
    assert page.eval("document.querySelectorAll('#gaps .ssr-badge').length") == 1


def test_reverse_splits_and_day_2_runners_are_badged(page, world):
    open_dashboard(page, world, "/#gaps")
    rs = page.wait("document.querySelector('#gaps a.sym[data-sym=ACME]')?.parentElement.querySelector('.rs-badge')"
                   " && [...document.querySelector('#gaps a.sym[data-sym=ACME]').parentElement.querySelectorAll('.rs-badge')]"
                   ".map(b => [b.textContent, b.title])", timeout=20)
    assert rs == [["RS 1:10", ("1-for-10 reverse split, effective Oct 15, 2026 (in 2 days), per the 8-K of Oct 9, 2026. "
                               "Every 10 shares become one, so the float is 10× smaller: freshly split stocks often run, "
                               "and offerings often follow.")]]
    d2 = page.wait("[...document.querySelectorAll('#gaps .d2-badge')].map(b => b.closest('tr').querySelector('a.sym')"
                   ".textContent + ' ' + b.title)", timeout=20)
    assert d2 == [("DYNA Day 2: up 50% in the last session (Oct 12, 2026). Second-day continuation is a classic setup, "
                   "and so is the fade.")]
    assert page.eval("!!document.querySelector('#movers a.sym[data-sym=DYNA]').parentElement.querySelector('.d2-badge')")
    assert page.eval("splitWhen({effective: '2026-10-10', filed: '2026-10-01'})") == "effective Oct 10, 2026 (3 days ago)"
    assert page.eval("splitWhen({effective: null, filed: '2026-10-01'})") == "announced Oct 1, 2026, date not found in the filing"


def test_estimated_luld_bands_show_in_the_panel_and_a_badge_when_near(page, world):
    open_dashboard(page, world)
    page.click("#watchlist a.sym[data-sym=BOLT]")
    # Only stocks near a band are pushed to the page; the panel gets the bands with the ticker's data.
    assert page.eval("Object.keys(state.luld)") == []
    stat = page.wait("[...document.querySelectorAll('#detail-stats div')].find(d => d.textContent.includes('LULD'))"
                     "?.innerText.includes('–1') && [...document.querySelectorAll('#detail-stats div')]"
                     ".find(d => d.textContent.includes('LULD')).innerText", timeout=20)
    assert stat.split() == ["LULD", "bands", "est.", "9.45–11.55"]  # 10.50 ± 10%
    page.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    # A stock near a band (the server's message, as it would send it):
    page.eval("""handle({type: 'luld', data: {...state.luld,
        FERN: {ref: 9.6, lower: 8.64, upper: 10.56, tier: 2, near: 'up', away_pct: 1.3}}})""")
    badge = page.wait("[...document.querySelectorAll('#watchlist .luld-badge')].map(b => [b.textContent, b.title])")
    assert badge == [["LULD ↑1.3%", ("1.3% from the limit-up band. Estimated LULD bands 8.64–10.56 around 9.60 (average "
                                     "of the last 5 minutes; Tier 2, ±10%). At a band the stock pauses for 5 minutes if "
                                     "it can't get back inside within 15 seconds. An estimate from the prices this app sees.")]]


def test_a_stock_that_traded_its_whole_float_gets_a_rot_badge(page, world):
    open_dashboard(page, world)
    rot = page.wait("[...document.querySelectorAll('#watchlist .rot-badge')].map(b => [b.closest('tr')"
                    ".querySelector('a.sym').textContent, b.textContent, b.title])", timeout=20)
    assert rot == [["BOLT", "ROT 1.3×", ("Volume today 1.0M is 1.3× the float of 800.00K shares: every share "
                                         "available to trade has changed hands at least once.")]]
    assert page.eval("document.querySelector('#rotation-alerts').checked") is True
    # Setup → Signal thresholds: at 2× the float, 1.3× is no longer a rotation; RVOL follows too.
    page.eval("handle({type: 'thresholds', data: {rvol_threshold: 0.5, rotation_multiple: 2, sympathy_min_pct: 5,"
              " sympathy_lead_pct: 30}})")
    assert page.eval("document.querySelectorAll('#watchlist .rot-badge').length") == 0
    assert page.text("#rvol-note").startswith("RVOL ≥ 0.5×")
    page.eval("handle({type: 'thresholds', data: {rvol_threshold: 2, rotation_multiple: 1, sympathy_min_pct: 5,"
              " sympathy_lead_pct: 30}})")
    assert page.eval("document.querySelectorAll('#watchlist .rot-badge').length") == 1
    assert page.text("#gaps-note").startswith("Gaps ≥ 3%")


def test_the_ticker_drawer_opens_with_its_dilution_panel_and_closes_on_escape(page, world):
    open_dashboard(page, world)
    page.wait("!!document.querySelector('#news a.sym[data-sym=ACME]')")
    page.click("#news a.sym[data-sym=ACME]")
    page.wait("!document.querySelector('#detail').hidden")
    assert page.wait("document.querySelector('#detail-dilution .risk')?.textContent") == "high"
    assert "Shelf" in page.text("#detail-dilution")
    levels = page.wait("document.querySelectorAll('#detail-levels li').length === 4"
                       " && [...document.querySelectorAll('#detail-levels li')].map(li => li.innerText)")
    assert levels[0].startswith("$12.50 outstanding warrants (average)") and levels[0].endswith("60% above the price")
    assert levels[1] == "$9.00 offering price (424B5 of Aug 1, 2026) 15% above the price"
    assert levels[2] == "now $7.80"
    assert levels[3].startswith("$7.50 Series A warrants for 1.00M shares") and levels[3].endswith("in the money")
    assert page.text("#detail-price .war-badge") == "WAR" and page.text("#detail-price .ssr-badge") == "SSR"
    page.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    page.wait("document.querySelector('#detail').hidden")  # closes via history, so a moment later


def chart_point(page, price):
    """Viewport coordinates of `price` on the drawer's chart, from the geometry it was drawn with."""
    return page.eval(f"""(() => {{
        const g = detail.geom, r = document.querySelector('#detail-chart').getBoundingClientRect();
        return [r.left + g.pad.l + g.plotW / 2, r.top + g.pad.t + (g.hi - {price}) / (g.hi - g.lo) * g.priceH];
    }})()""")


def test_the_chart_shows_the_levels_and_a_click_sets_an_alert_there(page, world):
    open_dashboard(page, world)
    page.wait("!!document.querySelector('#news a.sym[data-sym=ACME]')")
    page.click("#news a.sym[data-sym=ACME]")
    page.wait("document.querySelectorAll('#detail-levels li').length === 4 && !!detail.geom")
    labels = page.eval("chartLevels().map(l => `${l.label} ${l.price}`)")
    assert labels == ["Series A warrants 7.5", "warrants (avg) 12.5", "offering 424B5 9", "pre-market high 7.8"]
    assert page.text("#detail-stats").count("PM high") == 1
    try:
        page.click_at(*chart_point(page, 9.0))  # above the 7.80 price
        offer = page.wait("!document.querySelector('#chart-alert').hidden && document.querySelector('#chart-alert').innerText")
        assert offer.startswith("Alert when ACME goes above $9.00") and "Add to watchlist & set alert" in offer
        page.click("#chart-alert [data-chart-alert=set]")
        page.wait("state.alert_rules.some(r => r.symbol === 'ACME' && r.kind === 'above' && r.value === 9)")
        assert "ACME" in page.eval("state.watchlist") and page.eval("document.querySelector('#chart-alert').hidden")
        page.wait("chartLevels().some(l => l.label === 'alert ↑' && l.price === 9)")
        assert "above 9" in page.text("#detail-alerts")
        page.click_at(*chart_point(page, 7.6))  # below the price this time; then thought better of it
        assert page.wait("document.querySelector('#chart-alert').innerText").startswith("Alert when ACME goes below $7.6")
        page.click("#chart-alert [data-chart-alert=cancel]")
        assert page.eval("document.querySelector('#chart-alert').hidden") is True
    finally:  # the server is shared: put the watchlist back as the other tests expect it
        page.eval("""(async () => {
            for (const r of state.alert_rules.filter(r => r.symbol === 'ACME')) await api('DELETE', `/api/alert-rules/${r.id}`);
            if (state.watchlist.includes('ACME')) await api('DELETE', '/api/watchlist/ACME');
        })()""")
        page.wait("!state.watchlist.includes('ACME')")


def test_on_a_phone_a_tap_on_the_chart_offers_an_alert_without_covering_the_level(page, world):
    page.phone()
    page.goto(world[0].url + "/#ticker=ACME")
    page.wait("document.querySelectorAll('#detail-levels li').length === 4 && !!detail.geom", timeout=20)
    page.eval("document.querySelector('#detail-chart').scrollIntoView({block: 'center'})")
    x, y = chart_point(page, 9.9)  # near the top: no room for the offer above the point
    page.tap_at(x, y)
    box = page.wait("!document.querySelector('#chart-alert').hidden"
                    " && JSON.parse(JSON.stringify(document.querySelector('#chart-alert').getBoundingClientRect()))")
    assert page.text("#chart-alert").startswith("Alert when ACME goes above $9.9")
    assert not box["top"] <= y <= box["bottom"]  # the line tapped stays visible
    assert box["left"] >= 0 and box["right"] <= 390
    page.click("#chart-alert [data-chart-alert=cancel]")


def test_a_halt_counts_down_to_its_resumption_every_second(page, world):
    open_dashboard(page, world)
    badge = "document.querySelector('#movers a.sym[data-sym=DYNA]')?.parentElement.querySelector('.halt-badge .countdown')"
    first = page.wait(f"{badge}?.textContent")
    assert first.count(":") == 1  # m:ss, about a quarter of an hour out
    page.wait(f"{badge}?.textContent !== {first!r}", timeout=3)  # ticks without new data from the server
    page.click("[data-left-tab=halts]")
    row = page.wait("[...document.querySelectorAll('#halts tr')].find(r => r.textContent.includes('DYNA'))?.innerText")
    assert "09:55" in row and "in 1" in row  # resumes 09:55, in 1x:xx
    assert page.eval("document.querySelector('#tab-halts').scrollWidth <= document.querySelector('#tab-halts').clientWidth")
    # When a countdown runs out, the halts are redrawn at once (halted -> resumed), not on the next refresh.
    page.eval("""window._redraws = 0; const draw = refreshHalts; refreshHalts = () => { _redraws++; draw(); };
        document.body.insertAdjacentHTML('beforeend', `<span id="ending" data-until="${appNow() + 300}"></span>`)""")
    page.wait("window._redraws >= 1 && document.querySelector('#ending').textContent === 'now'", timeout=2)
    page.eval("document.querySelector('#ending').remove()")


def test_reactions_tab_loads_its_report_and_the_recap_preview(page, world):
    open_dashboard(page, world)
    page.click("[data-left-tab=reactions]")
    page.wait("document.querySelector('#reactions-note').textContent.includes('tracked')")
    assert page.text("#reactions-note").endswith("alerts tracked")
    # The made-up morning fires no alerts while the tests run: show the table a report that has some.
    page.eval("""(async () => {
        const real = api;
        api = async (method, url) => url.startsWith('/api/reactions?') ? {
            days: 30, tracked: 3, catalysts: 1, by_type: [], offerings_by_risk: [], by_context: [], recent: [],
            gaps: [{group: 'gap up · low float', n: 4, measured: 4, median_5m: 1.0, median_30m: 6.5, median_close: -3.0,
                    down_share: 0.75}],
            luld: {n: 9, median_error_pct: 0.8, within_1_pct: 0.67, within_3_pct: 1, warned_share: 0.44, worst_error_pct: 2.1},
            alerts: [{group: 'broke pre-market high', n: 2, measured: 2, median_5m: 3.2, median_30m: -1.5,
                      median_close: -6.0, down_share: 1}]} : real(method, url);
        try { await loadReactions(); } finally { api = real; }
    })()""")
    assert page.text("#reactions-note") == "1 catalysts, 2 alerts tracked"
    assert page.text("#reactions-alerts tr").split() == ["broke", "pre-market", "high", "2", "·", "2", "+3.2%",
                                                          "-1.5%", "-6.0%", "100%"]
    assert page.text("#reactions-gaps tr").split() == ["gap", "up", "·", "low", "float", "4", "·", "4", "+1.0%",
                                                        "+6.5%", "-3.0%", "75%"]
    assert page.text("#reactions-luld") == ("9 pauses: the estimate was off by 0.8% (median), within 1% in 67% and within "
                                            "3% in 100% of them (worst 2.1%). Flagged near the band beforehand: 44%.")
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
    # The offering's reaction is recorded once its risk profile is read: fetch until it's in.
    text = page.wait(f"fetch({json.dumps(href)}).then(r => r.text()).then(t => t.includes(',ACME,') && t)", timeout=20)
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


def test_sympathy_movers_are_badged_and_the_panel_lists_the_industry(page, world):
    open_dashboard(page, world)
    sym = page.wait("document.querySelector('#movers a.sym[data-sym=DYNA]')?.parentElement.querySelector('.sym-badge')?.title",
                    timeout=20)
    assert sym == ("Up 12.0% in sympathy with CRUX +41% (Biotechnology). The ticker panel lists the industry's movers.")
    page.click("#movers a.sym[data-sym=DYNA]")
    rows = page.wait("document.querySelectorAll('.industry-movers li').length === 2"
                     " && [...document.querySelectorAll('.industry-movers li')].map(li => li.innerText.split(/\\s+/).slice(0, 2).join(' '))")
    assert rows == ["CRUX +41.00%", "DYNA +12.00%"]
    assert page.eval("document.querySelector('#detail-industry h3').textContent").startswith("Same industry today Biotechnology")
    assert page.eval("document.querySelector('.industry-movers li.self a').textContent") == "DYNA"


def test_signal_thresholds_can_be_tuned_in_setup(page, world):
    open_dashboard(page, world)
    page.click("#setup-btn")
    page.wait("!!document.querySelector('#set-breakout_pullback_pct')")
    group = page.eval("document.querySelector('#set-breakout_pullback_pct').closest('.set-group').textContent")
    assert group.split()[:2] == ["Signal", "thresholds"]
    for label in ("New high of day counts after a pullback of (%)", "Day 2 after a gain of (%)",
                  "Float rotation at volume / float of", "Near a LULD band within (% of the band)",
                  "A stock leads its industry when up (%)", "Moving in sympathy when up (%)",
                  "Quote an alert's track record after (measured)"):
        assert label in group


def test_the_alert_kinds_panel_counts_and_mutes_and_a_stock_can_be_snoozed(page, world):
    open_dashboard(page, world)
    page.click("#alert-kinds-box summary")
    rows = page.wait("document.querySelectorAll('#alert-kinds tbody tr:not(.group)').length")
    assert rows == 20
    groups = page.eval("[...document.querySelectorAll('#alert-kinds tr.group')].map(r => r.textContent)")
    assert groups == ["Your rules", "Volume", "Breakouts", "Levels from filings", "Halts", "News and filings"]
    phone = "#alert-kinds [data-mute-kind=pmh][data-where=phone]"
    try:
        page.click(phone)  # off
        page.wait(f"!document.querySelector('{phone}').checked && state.alert_mutes.pmh?.phone === true")
        kinds = {k["kind"]: k for k in world[0].get("/api/alert-kinds").json()["kinds"]}
        assert kinds["pmh"]["phone"] is False and kinds["pmh"]["browser"] is True
    finally:
        page.eval("api('PUT', '/api/alert-kinds/pmh', {phone: true, browser: true})")
        page.wait("!state.alert_mutes.pmh")
    # Snooze from the log (an alert as the server would send it):
    page.eval("""handle({type: 'alerts', data: [{id: 'a1', time: new Date(appNow()).toISOString(), symbol: 'FERN',
        kind: 'move', message: 'FERN down 6.0% today', price: 9.4}]})""")
    try:
        page.click("#alert-log [data-snooze=FERN]")
        page.wait("state.snoozed.FERN === etDate() && !document.querySelector('#alert-log [data-snooze=FERN]')")
        page.wait("!!document.querySelector('#alert-kinds [data-unsnooze=FERN]')")
        page.click("#alert-kinds [data-unsnooze=FERN]")
        page.wait("!state.snoozed.FERN && !!document.querySelector('#alert-log [data-snooze=FERN]')")
    finally:
        page.eval("state.snoozed.FERN && api('DELETE', '/api/snooze/FERN')")


def test_a_backup_downloads_without_secrets_and_restores_after_a_preview(page, world, tmp_path):
    server = world[0]
    download = server.get("/api/backup")
    assert download.headers["content-disposition"].startswith('attachment; filename="market-pulse-backup-')
    assert download.headers["cache-control"] == "no-store"
    original = download.json()
    assert set(original) == {"market_pulse_backup", "app", "exported", "state", "settings"}
    assert TOKEN not in download.text and TOPIC not in download.text
    edited = {**original, "state": {**original["state"], "keywords": [*original["state"]["keywords"], "uplisting"]},
              "settings": {**original["settings"], "ntfy_topic": "mp-must-not-come-back-0000"}}
    (tmp_path / "backup.json").write_text(json.dumps(edited))
    open_dashboard(page, world)
    page.click("#setup-btn")
    page.wait("!!document.querySelector('[data-restore-file]')")
    try:
        page.choose_file("[data-restore-file]", tmp_path / "backup.json")
        preview = page.wait("document.querySelector('#restore-preview').innerText")
        assert preview.startswith("1 change from backup.json:")
        assert "Keywords" in preview and "uplisting" in preview
        assert "never restored" in preview and "mp-must-not-come-back" not in preview
        page.click("[data-restore-apply]")
        page.wait("state.keywords.includes('uplisting')")
        assert "mp-must-not-come-back" not in server.get("/api/settings").text
    finally:
        page.eval(f"api('POST', '/api/backup/restore', {json.dumps(original)})")
        page.wait("!state.keywords.includes('uplisting')")


def test_the_backup_api_turns_away_what_isnt_a_backup(world):
    server = world[0]
    assert server.post("/api/backup/preview", content=b"not json", headers={"content-type": "application/json"}).status_code == 422
    assert server.post("/api/backup/preview", json={"state": {}}).status_code == 422
    assert server.post("/api/backup/preview", content=b"x" * 300_000,
                       headers={"content-type": "application/json"}).status_code == 413


def test_the_game_plan_lists_the_gappers_with_their_flags_and_opens_a_ticker(page, world):
    open_dashboard(page, world)
    page.wait("state.gaps.length >= 4", timeout=20)
    page.click("#plan-btn")
    cards = page.wait("document.querySelectorAll('.plan-card').length >= 4"
                      " && [...document.querySelectorAll('.plan-card .plan-head .sym')].map(a => a.textContent)")
    assert cards == ["CRUX", "ACME", "DYNA", "BOLT"]  # with a catalyst first, then by gap
    flags = page.wait("[...document.querySelectorAll('.plan-card')].find(c => c.querySelector('.sym').textContent === 'BOLT')"
                      ".querySelectorAll('.flag').length >= 3"
                      " && [...[...document.querySelectorAll('.plan-card')].find(c => c.querySelector('.sym').textContent"
                      " === 'BOLT').querySelectorAll('.flag')].map(f => f.textContent)", timeout=20)
    assert "low float" in flags and "watchlist" in flags
    page.click(".plan-card .plan-head .sym[data-sym=ACME]")
    page.wait("!document.querySelector('#detail').hidden && document.querySelector('#plan').hidden")
    page.eval("history.back()")
    page.wait("document.querySelector('#detail').hidden && !document.querySelector('#plan').hidden")
    page.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    page.wait("document.querySelector('#plan').hidden")
    page.goto(world[0].url + "/?from=bookmark#plan")  # a fresh load, as from a bookmark
    page.wait("!document.querySelector('#plan').hidden && document.querySelectorAll('.plan-card').length >= 1", timeout=20)
    # Once enough gappers like it were measured, a card says how they did:
    page.eval("""plan.data.entries[0].records = [{group: 'gap up · low float', measured: 14, median_30m: 2.1,
        median_close: -3.4, down_share: 0.6}]; renderPlan()""")
    assert page.text(".plan-card .record") == "gap up · low float: 10:00 +2.10%, close -3.40%, 60% faded (14 in 30 days)"


def test_real_time_ticks_update_just_their_rows(page, world):
    open_dashboard(page, world)
    page.eval("handle({type: 'quote_ticks', data: [{...state.quotes.find(q => q.symbol === 'BOLT'), price: 11.11, live: true,"
              " price_time: Math.floor(appNow() / 1000)}]})")
    rows = page.eval("[...document.querySelectorAll('#watchlist tr')].map(r => r.innerText)")
    assert any(r.startswith("BOLT") and "\t11.11\t" in r for r in rows)
    assert any(r.startswith("FERN") and "\t9.80\t" in r for r in rows)  # untouched
    assert page.eval("state.quotes.length") == 2


def test_a_burst_of_alerts_makes_three_desktop_notifications_and_a_summary(page, world):
    open_dashboard(page, world)
    page.eval("window.shown = []; window.Notification = class { static permission = 'granted';"
              " constructor(title, options) { if (title === 'Market Pulse' || options.tag?.startsWith('burst'))"
              " shown.push([title, options.body]); } }")  # only this burst: the replay may alert meanwhile
    page.eval("handle({type: 'alerts', data: ['ACME', 'BOLT', 'CRUX', 'DYNA', 'ECHO'].map((s, i) => ({id: `burst${i}`,"
              " time: new Date(appNow()).toISOString(), symbol: s, kind: 'above', message: `${s} crossed above 10`}))})")
    assert page.eval("shown") == [["ACME · price alert", "ACME crossed above 10"],
                                  ["BOLT · price alert", "BOLT crossed above 10"],
                                  ["CRUX · price alert", "CRUX crossed above 10"],
                                  ["Market Pulse", "+2 more alerts"]]


def test_a_stale_real_time_price_is_marked_as_delayed(page, world):
    open_dashboard(page, world)
    page.eval("handle({type: 'quote_ticks', data: [{...state.quotes.find(q => q.symbol === 'BOLT'), live: false, delayed: true}]})")
    assert "Yahoo" in page.eval("document.querySelector('#watchlist .live-dot.delayed').title")


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


# --- your data (last: it deletes the replay's reaction history) -------------------------------------

def test_setup_lists_your_data_and_deletes_the_reaction_history_after_confirming(page, world):
    on_a_phone(page, world)
    page.click("#setup-btn")
    page.wait("document.querySelectorAll('.data-list li').length >= 7")
    rows = page.eval("[...document.querySelectorAll('.data-list li b')].map(b => b.textContent)")
    assert rows == ["Watchlist, keywords, alert rules, mutes and snoozes", "Settings saved in Setup",
                    "Catalyst reaction history", "Recordings for replay", "HTTPS certificates", "LULD estimate checks",
                    "Alert counts", "Log",
                    "This browser"]
    html = page.eval("document.querySelector('#setup').innerHTML")
    assert "/home/" not in html and "/tmp/" not in html  # paths are shown relative, never in full
    assert page.eval(OFF_SCREEN.replace("SCOPE", "#setup *")) == []
    before = page.eval("[...document.querySelectorAll('.data-list li')].find(li => li.textContent.includes('reaction history')).innerText")
    assert " 0 catalysts" not in before
    page.eval("window.confirm = () => false")  # cancelling keeps everything
    page.click("[data-delete-data=reactions]")
    assert page.eval("!!document.querySelector('[data-delete-data=reactions]')")
    page.eval("window.confirm = () => true")
    page.click("[data-delete-data=reactions]")
    page.wait("[...document.querySelectorAll('.data-list li')].find(li => li.textContent.includes('reaction history'))"
              ".innerText.includes('0 events')")
    assert not page.eval("!!document.querySelector('[data-delete-data=reactions]')")  # nothing left to delete
    assert world[0].get("/api/reactions?days=30").json()["tracked"] == 0


def test_forgetting_this_browser_clears_what_it_stored(page, world):
    open_dashboard(page, world)
    page.click("#setup-btn")
    page.wait("localStorage.getItem('mp-setup-seen-v1') === '1' && !!document.querySelector('[data-forget-browser]')")
    page.eval("window.confirm = () => true")
    page.click("[data-forget-browser]")
    page.wait("document.readyState === 'complete' && localStorage.getItem('mp-setup-seen-v1') === null"
              " && document.querySelectorAll('#watchlist tr:not(.empty)').length === 2")
