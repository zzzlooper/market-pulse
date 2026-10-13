# Market Pulse

A one-screen stock dashboard in the spirit of FoxRunner: live watchlist prices, top movers,
unusual-volume scanner and a real-time news feed with keyword alerts. It uses only free data.
It runs without any API key, and a free Finnhub key adds real-time watchlist prices.

## Run

Requires Python 3.11+.

```sh
python -m venv .venv
source .venv/bin/activate        # fish: source .venv/bin/activate.fish
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000. The **Setup** panel opens with a checklist: your contact email for
SEC, phone alerts, the background service and the optional extras, each with a button to fix
it. Then click **Enable alerts** to get desktop notifications and a sound.

### Settings and privacy

Most settings can be changed in **Setup**; the rest (intervals, feeds) live in `config.toml`
(`cp config.example.toml config.toml`). Layers, lowest to highest: defaults, `config.toml`,
the dashboard, environment variables. A setting that comes from an environment variable is
shown locked in the dashboard. In `config.toml`, put top-level settings before any
`[section]`: TOML files anything after `[feeds]` under it, so the app refuses a feed that
isn't a web address rather than silently ignoring, say, a misplaced `access_token`.

- Dashboard values are saved in `data/settings.json`, readable by you only, in a folder git
  ignores.
- Secrets (tokens, the ntfy topic, Telegram ids, your SEC email) are write-only: Setup shows
  whether each is set and where from, with at most its last few characters, and never sends a
  saved one back. *Generate* makes a strong access token or ntfy topic and shows it once, to
  copy to your phone. No setting's value is ever logged.
- Changes apply at once, except the Finnhub key, which needs a restart.
- Without an access token the app answers only requests addressed to `localhost`/`127.0.0.1`,
  and it always refuses changes sent from other websites. A page you visit can't change your
  settings, even by pointing its own domain at your computer (DNS rebinding).

Tests: `pip install pytest && pytest`. They never touch the network, your `config.toml` or
your `data/` folder.

`tests/test_scenarios.py` replays made-up trading days through the real engine and checks
what would reach the browser and the phone, e.g. an offering on a top gainer gives exactly one
phone alert with its dilution risk, and its drop by the close shows in the recap. Write new
ones with `tests/scenario_kit.py`: invented companies, no people, nothing sent anywhere.

`tests/test_browser.py` opens the dashboard in headless Chromium (skipped if it isn't
installed) against the app replaying a made-up morning, and fails on any JavaScript error:
panels fill, badges show, tabs switch, the ticker and Setup drawers work, a saved secret is
never shown back, and an access token is asked for. The app gets a scrubbed environment and
throwaway config and data; Chromium a throwaway profile with its background traffic off and
every non-local request sent to a dead proxy (`tests/browser_kit.py`).


**Your data** (in Setup) lists everything Market Pulse keeps on this computer, with its size,
counts and dates, but never its contents: your watchlist and rules, settings saved in Setup
(by name only), the reaction history, recordings, where the log goes, and what this browser
stored. Paths are shown relative to the project, never in full (that would show your user
name).

- *Delete* clears the reaction history, the recordings (except one being written) or the
  settings saved in Setup, after a confirmation. Deleted history is compacted out of the
  database file, not just marked free.
- The reaction history is kept `reactions_keep_days` (365; 0 = forever) and pruned daily.
- *Forget* clears what this browser stored and signs it out, e.g. on a borrowed device.
- Every file under `data/` is readable by you only.
### Real-time prices (optional)

Get a free key at https://finnhub.io/register, then either set `finnhub_api_key` in
`config.toml` or run with `FINNHUB_API_KEY=... uvicorn app.main:app`.

- Watchlist prices then stream from Finnhub's trade WebSocket and are pushed to the browser
  at most twice a second. A green dot next to a price marks it as real-time; hover it to see
  the last trade time.
- A REST quote snapshot (`finnhub_quotes_interval`, default 5 min) fills in prices before the
  first trade, e.g. when the market is closed, and keeps the previous close current.
- Volume and RVOL still come from Yahoo. Finnhub's stream only counts trades since the app
  started, so it can't give a day's total volume.
- Top movers stay on Yahoo, because Finnhub's free tier has no screener.
- The free tier streams up to 50 symbols, US stocks only. Indices (`^GSPC`) and futures stay
  on delayed Yahoo prices, and so does any symbol if the key is missing or rejected.

### Phone alerts (optional)

Alerts can be pushed to your phone through **ntfy**, **Telegram**, or both. Nothing is sent
until one is configured. Then use **Send test** in the Alerts panel's *Phone* box to check.

**ntfy** (no account needed):
1. Install the ntfy app ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) /
   [iOS](https://apps.apple.com/app/ntfy/id1625396347)).
2. Make up a hard-to-guess topic name. On the public ntfy.sh server **anyone who knows the
   topic can read it**, so treat it like a password:
   `python -c "import secrets; print('mp-' + secrets.token_urlsafe(12))"`
3. Subscribe to that topic in the app, and put it in `config.toml`: `ntfy_topic = "mp-…"`.
   For a self-hosted server or a protected topic, also set `ntfy_server` and `ntfy_token`.

**Telegram:**
1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the bot token.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": …}`.
3. Set `telegram_bot_token` and `telegram_chat_id` in `config.toml`.

Secrets can come from environment variables instead of `config.toml`: `NTFY_TOPIC`,
`NTFY_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (and `FINNHUB_API_KEY`, `ACCESS_TOKEN`,
and `SEC_USER_AGENT` for your contact address).

- The *Phone* box has switches for which alerts go to the phone: price, halts, news (keyword
  and watchlist headlines), SEC filings, unusual volume, the pre-market brief, the daily recap and data outages.
- *Daily recap*: weekdays at `recap_time` (default 16:30 ET), one message with the watchlist's
  moves, the day's halts (yours named), filings by type, the biggest reactions by the close
  (see *Catalyst reactions*) and any data source that's down. Skipped on market holidays.
  *Reactions* tab → *Today's recap* previews it and can send it right away. It names your
  watchlist tickers, so on public ntfy.sh keep the topic secret. Unusual volume is off by default,
  since it can fire dozens of times a day.
- *Data outages*: when a data source has been down for `source_alert_minutes` (default 5;
  0 turns it off), you get one message saying which alerts can't fire, and another when it's
  back. Silence from a broken feed otherwise looks the same as a quiet market.
- A burst of alerts is sent as 2 messages plus a "+N more" summary, with a hard cap of 20 per
  minute. Halts arrive as urgent notifications, price alerts as high priority, news as normal.
- Set `public_url` (e.g. `http://192.168.1.20:8000`) to make a tapped alert open that
  ticker's detail panel. That only works where your phone can reach the app, e.g. on the same Wi-Fi,
  so set an access token first (next section).
- Delivery failures appear under *Phone* in red. Tokens and topics are kept out of error
  messages and logs.

### Keep it running in the background

Phone alerts only arrive while the app runs. To keep it running after you close the terminal,
install it as a systemd user service:

```sh
sh scripts/install-service.sh --enable      # MP_HOST=0.0.0.0 / MP_PORT=8000 to change where it listens
journalctl --user -u market-pulse -f         # logs
loginctl enable-linger                       # optional: keep running while you're logged out
sh scripts/install-service.sh --uninstall
```

- The script writes `~/.config/systemd/user/market-pulse.service`, which holds no secrets, and
  creates `~/.config/market-pulse/env` (mode 600, only you can read it). Put your
  `SEC_USER_AGENT` and any tokens there instead of in the project folder; `config.toml` works too.
- It refuses `MP_HOST=0.0.0.0` until an access token is set (next section), and never prints
  your settings' values.
- Access logs are off, so the journal doesn't record which devices opened the dashboard. Files
  the app writes (`data/state.json`) are readable by you only.
- Rerun the script after changing `MP_HOST`/`MP_PORT`; after editing the env file or
  `config.toml`, `systemctl --user restart market-pulse`.

### Record and replay

Record a trading day, then replay it on a weekend to test alerts, the recap and the dashboard
without waiting for the market:

```sh
# record: turn on "Record the day's data for replay" in Setup (or record = true in config.toml,
# or MARKET_PULSE_RECORD=1), restart, and let it run
python -m app.replay list
python -m app.replay play data/recordings/2026-10-13_092500.jsonl.gz --from 15:45 --speed 10
# then open http://localhost:8001
```

- A recording holds what the data sources answered: Yahoo results, the news and halt feeds,
  SEC feeds and documents. Never the requests themselves, so your SEC contact address, tokens
  and cookies aren't in it, and URLs lose any credential-like parameter.
- SEC insider (Form 4) and 13D documents name private people, often with addresses and phone
  numbers. They're cut down as they're recorded to the fields the app reads: no names (except
  firms the app itself would show), addresses, signatures or free text. Feed entries naming an
  insider or investor are redacted too.
- Feeds are stored item by item and only when something changed, so a day is a few tens of MB.
  Files are readable by you only; the newest `record_keep` (default 10) are kept.
- A replay never uses the network (anything not recorded shows as a data outage) and never
  sends phone alerts. It runs on the recorded watchlist and rules in a temporary folder, so your
  real watchlist, settings and reaction history aren't touched. A banner shows it's a replay;
  the clock, the market session and "today" follow the replayed time, at `--speed`.
- Ticker details work for the tickers whose charts were fetched while recording.

### Opening it from your phone or another computer

`uvicorn app.main:app` listens on `127.0.0.1`, so only this computer can open it. To reach it
from your phone, it has to listen on the network, and then anyone on that Wi-Fi could change
your watchlist, delete alert rules or switch phone alerts off. Set an access token first:

```sh
python -c "import secrets; print(secrets.token_urlsafe(24))"   # put it in config.toml: access_token = "…"
uvicorn app.main:app --host 0.0.0.0
```

- Each browser asks for the token once, then stays signed in for a year (an HttpOnly,
  SameSite=Strict cookie). Scripts can send `Authorization: Bearer <token>` instead.
- Every `/api/` route and the live WebSocket need it. The page itself loads without it, so it
  can show the sign-in prompt.
- The WebSocket refuses pages served by other websites, token or not, so a site you visit
  can't read your dashboard through `localhost`.
- The app logs a warning at startup if `public_url` is set without a token.
- Over plain HTTP, anyone who can watch your network traffic can see the token and the
  dashboard. That's fine on your own password-protected home Wi-Fi, never on public or shared
  Wi-Fi. To encrypt it, use HTTPS (next section); Setup's checklist says which one you're on.

### HTTPS with Market Pulse's own certificate

```sh
MP_HOST=0.0.0.0 MP_HTTPS=1 sh scripts/install-service.sh --enable   # or: python -m app.serve --host 0.0.0.0 --https
```

Market Pulse creates a small certificate authority (CA) of its own, once, and with it a server
certificate for this computer's private addresses (`data/tls/`). Install the CA certificate on
your phone once; after that the dashboard opens with no warnings, also after renewals or when
the computer gets a new address.

1. On the phone, open `https://<computer's address>:8000/market-pulse-ca.crt`. The first time
   the browser warns that it doesn't trust the connection yet: continue, just for this.
2. iPhone: Settings › General › VPN & Device Management › install the "Market Pulse local CA"
   profile, then Settings › General › About › Certificate Trust Settings › turn on full trust.
   Android: Settings › Security › Encryption & credentials › Install a certificate › CA
   certificate, and pick the downloaded file.
3. Compare the fingerprint the phone shows with the one in Setup (*Secure phone access*).

Safety:
- The CA is name-constrained to `localhost` and private network addresses (192.168.x.x,
  10.x.x.x, 172.16–31.x.x, 100.64.x.x, fc00::/7). Even if its key leaked, it couldn't vouch for
  any public website on your phone: browsers reject such certificates (a test forges one for
  `bank.example` with the CA's key and checks it's refused).
- Both private keys stay in `data/tls/`, readable by you only, and are never shown or sent;
  only the CA certificate, which is public, can be downloaded. The sign-in cookie becomes
  HTTPS-only.
- The server certificate lists addresses only, not this computer's name: anyone who connects
  sees the certificate, and computer names often contain a person's name. So open the dashboard
  by address (`https://192.168.1.20:8000`).
- The server certificate lasts 397 days (what phones accept) and is renewed, along with any new
  address, every time Market Pulse starts; Setup warns when a restart is due.

On a phone the dashboard is one column: watchlist, news, the tabs (unusual volume, halts,
gaps, reactions), movers, then alert settings. A tapped alert opens the ticker full-screen,
and the phone's Back gesture closes it (and Setup) back to the dashboard. Small phones drop
the watchlist's raw volume column (below 420px wide) and its 24h news column (below 340px).
Browser tests check it at 390, 360 and 320px wide.

To install it like an app, open the dashboard on the phone and choose *Add to Home Screen*
(Safari: Share menu; Chrome: ⋮ menu). It gets the Market Pulse icon and opens full-screen,
without the browser bar. On iPhones the home-screen app keeps its own sign-in, so enter the
access token once more there. Over plain HTTP on your network Android adds a shortcut that
opens in Chrome; the full app treatment there needs HTTPS (a reverse proxy, or Tailscale).
There's deliberately no offline cache: nothing from the dashboard is stored on the phone.

## What's on screen

| Panel | Source | Refresh |
|---|---|---|
| Watchlist: price, % change, volume, RVOL | Yahoo daily bars (`yfinance.download`); prices from Finnhub if configured | 15 s (Finnhub: streaming) |
| Top movers: gainers, losers, most active, small caps | Yahoo predefined screeners | 60 s |
| Unusual volume | Watchlist plus every screener result with RVOL ≥ threshold | with the above |
| News | SEC 8-K filings, GlobeNewswire, PR Newswire, Yahoo per-ticker RSS | 30 s |
| SEC filings (in the news feed) | EDGAR current filings: offerings, Form 4, Schedule 13D, 8-K | 30 s |
| Dilution risk (ticker detail) | SEC filing history and XBRL financials (data.sec.gov) | on open, cached 6 h |

- **RVOL** (relative volume) is today's volume divided by the 10-day average volume, scaled
  by how much of the 9:30–16:00 ET session has elapsed. Scaling linearly over-flags stocks
  near the open, because real intraday volume is heaviest at the open and close.
- **Tickers on news** come from patterns like `(NASDAQ: ACME)` and `$ACME`, from the SEC
  CIK→ticker map for filings, and from the feed itself for Yahoo per-ticker news.
- **Alerts** fire for new headlines that mention a watchlist ticker or match a keyword.
  Items already in a feed when the app starts, or when you add a ticker, don't alert.
- **Ticker detail** — click any ticker anywhere (watchlist, movers, news, halts, alert log) to
  open a side panel with everything about it:
  - an intraday chart: 1 day of 1-minute bars including pre/post-market (shaded), or 5 days
    of 5-minute bars. It has a previous-close line and volume bars, news dots coloured by
    sentiment, and red bands where the stock was halted. Hover for the bar's OHLC, volume,
    change and any headlines from that minute.
  - key stats: open, gap, day and 52-week range, volume vs average, RVOL, market cap, float,
    short % of float, days to cover, P/E, beta and next earnings date.
  - the ticker's news, halts, alert rules (add or remove them here) and fired alerts. These
    update live while the panel is open. Chart and stats refresh every 30 s.
  - **Dilution risk**: *high*, *medium*, *low* or *unknown*, with every reason and its points,
    from SEC data only (`app/dilution.py`):
    - filing history: a pending S-1 (+2), an active shelf (+1), offerings in the last 12 months
      (+1, or +2 for two or more), private share sales (8-K 3.02, +1), a delisting notice (+1);
    - warrants, from the first pages of the 3 newest offerings and private sales of the past
      year: sold with an offering (+1), and outstanding warrants worth 10% or more of the shares
      (+1; smaller overhangs are noted without points). The panel's *Warrants* figure shows the
      outstanding shares and their average exercise price, with the filing it came from;
    - XBRL financials: cash, monthly operating burn and the months of cash left at that rate
      (+2 under 6 months, +1 under 12; positive cash flow −1), and the share count against a
      year earlier (+2 for 50% more, +1 for 20%).
    - 4 points or more is *high*, 2–3 *medium*. Without recent financials (ETFs, trusts, many
      foreign filers) a quiet history is *unknown*, not *low*. The dilution-related filings of
      the last 2 years are listed with links.
    - The financials lag by up to a quarter, and SEC share counts aren't split-adjusted: a
      count from before a reverse split is marked. It's a starting point for your own reading,
      not a verdict.
    - Large, well-known issuers (an automatic S-3ASR shelf) mostly sell debt, so for them
      offerings and the shelf score nothing and the share count is what counts.
    - A **DIL** badge next to a ticker in every table marks high (red) or medium (amber) risk;
      hover it for the reasons. Profiles are kept fresh (every 6 hours) for the watchlist and
      the gainers and small-cap lists (`risk_movers`, at most `risk_max_symbols` = 60), fetched
      one at a time so news and filings aren't held up.
  - *Add to watchlist*, *Filter news* (the old click behaviour), and the company description.
  - Esc or a click outside closes it. `http://localhost:8000/#ticker=AAPL` opens straight to
    a ticker. Times in the panel are ET, to match the chart.
  - Data comes from Yahoo and is delayed like the rest. Company facts are cached for 10
    minutes and bars for 30 s.
- **Price & volume alerts** (bottom-right panel): rules like *AAPL above 350*,
  *all watchlist tickers move ±5%* or *RVOL ≥ 3×*. Leave the ticker blank to apply a rule to
  the whole watchlist. A toggle adds *alert when a stock enters the unusual-volume list*,
  at most once per stock per day.
  - Rules fire when a level is **crossed**, not while it holds. The first reading after
    startup, or after adding a rule, only records a baseline, so a stock already up 6%
    when the app starts won't alert until it dips below 5% and crosses again. A stock
    hovering around a level alerts at most every 30 minutes.
  - Alerts go to the log in the panel, a desktop notification and a lower-pitched beep than
    news alerts.
  - With Finnhub configured, rules are checked on every real-time price update; otherwise on
    each Yahoo refresh (~15 s, prices delayed). Default rules: all tickers ±5%, RVOL ≥ 3×.
    Rules are saved in `data/state.json`.
- **Trading halts** (*Halts* tab, bottom left): every US halt from Nasdaq Trader's free RSS
  feed, polled every 20 s. That covers news halts (T1/T2), regulatory halts (T12, H10…),
  limit-up/limit-down volatility pauses (LUDP) and circuit breakers.
  - Shows halt time, reason (hover for the full description), scheduled resumption and a
    live status: *Halted*, *Resumes in Nm* or *Resumed*.
  - *Today* is the default view. *All in feed* adds long-running halts, some of them years old.
  - A red **⏸ N halted** counter in the header shows today's active halts; click it to jump
    to the list. Halted tickers get a **HALTED** badge in every table.
  - Alerts (setting in the Alerts panel): *Watchlist* (default), *All stocks* or *Off*. They
    fire when a halt appears and again when a news halt gets its resumption time. The feed
    contents at startup are the baseline and don't alert. *All stocks* is noisy: a busy day
    has 40+ volatility pauses.
  - Open `http://localhost:8000/#halts` to start on the Halts tab.
- **Sentiment**: every headline is scored −1…+1 by a local finance lexicon (`app/sentiment.py`),
  with no API calls. Items at ±0.35 or beyond get a ▲ bullish / ▼ bearish badge, and hovering
  the badge shows the phrases that drove the score. The watchlist's *News 24h* column is the
  mean score of each ticker's headlines over the last day.
  - Longest phrase wins: "public offering" −2 beats "offering" −1, and "initial public
    offering" is neutral. A negator up to 3 words before a phrase flips it, so "did not meet
    its primary endpoint" is bearish.
  - It's a word-matching model, so it misses sarcasm and context ("growth" in a product
    name still counts as bullish). To tune it, edit `LEXICON_SOURCE` and add a case to
    `tests/test_sentiment.py`.
- **SEC filings** (`app/filings.py`): filings that move small caps appear in the news feed
  with a coloured badge, the form type and a one-line note on what the filing means.
  - *Dilution* (red): offering registrations (S-1, F-1), shelf registrations (S-3, F-3),
    priced offerings and shelf takedowns (424B1/B4/B5) and resale prospectuses (424B3/B7).
    Only listed companies are shown. 424B2 is skipped: banks file thousands a day for
    structured notes.
  - *Insider buy* (green): open-market purchases on Form 4 worth at least `insider_min_value`
    (default $50K), from any company, with the insider's role, size, average price and how much
    the holding grew. Trades under a 10b5-1 plan are marked. *Insider sales* show only for
    watchlist stocks. Awards, option exercises and tax withholding are ignored.
  - *13D stake* (blue): an investor crossing 5% with an active intent, with the stake size.
  - **Warrants**: for offerings and private share sales (8-K 3.02), the first pages of the
    filing are read for warrants (`app/warrants.py`). If the offering sells them, the alert and
    news item say *· with warrants* and give what the cover states, e.g. *pre-funded warrants for
    400K shares, warrants for 43.6M shares (Series D) at $0.66*. Warrants already outstanding go
    into the item's description. Shelf boilerplate ("we may offer… warrants") doesn't count. Best
    effort: wording varies, so a miss means "not on the first pages", not "none". Only the
    numbers are kept; no text from the filing, which can name people.
  - *8-K events* (amber): every 8-K is labelled by its items (earnings, material agreement…).
    The notable ones get an amber badge and a note on what they mean: delisting notice (3.01),
    unregistered share sale (3.02, dilution that never shows up as a prospectus),
    shareholder-rights change (3.03, usually a reverse split), auditor change (4.01),
    financials unreliable (4.02), bankruptcy (1.03), debt acceleration (2.04), change in
    control (5.01) and cybersecurity incident (1.05). Any watchlist 8-K alerts; for movers
    only the notable ones do.
  - Before 10:00 ET a stock in the Gaps list counts as moving too, e.g. *ACME · offering from
    shelf (gapping -22% pre-market, dilution risk HIGH)*.
  - A filing alerts (log, desktop, phone) when the stock is on the watchlist **or on today's
    movers or unusual-volume lists**. An offering right after a run-up is the classic case,
    and the phone message says why, with the dilution risk when it's high or medium, e.g.
    *ACME · offering from shelf (top gainer +45%, dilution risk HIGH)*.
    Filings already in the feeds at startup don't alert.
  - Privacy: Form 4 and 13D filings name insiders and individual investors, often with their
    addresses. Only roles (CEO, Director), share counts, prices and stake sizes are read. A 13D
    filer is named only when it is clearly a firm (LLC, LP, Ltd, Fund, Capital…), never a
    person. Your `sec_user_agent` is sent only to SEC hosts (www.sec.gov, data.sec.gov), by one
    client that refuses redirects, and is never logged. The dilution panel passes on only the
    figures it computes, none of the addresses or phone numbers in SEC's responses.
  - `sec_filings = false` turns it off (8-Ks then show as plain news, as in v0.1). EDGAR doesn't accept filings on weekends and federal
    holidays, and insiders have two business days to file a Form 4.
- **Earnings** (`app/earnings.py`): the next report date of each watchlist stock, looked up
  every 6 hours and when the watchlist changes. An **ER** badge marks reports within 5 days
  (filled on the day; hover for when). The 09:15 brief lists the watchlist's reports today, and
  the recap tonight's and the next trading day's. Yahoo's dates are reliable but its times often
  aren't (a 15:00 placeholder is common), so a time is only shown when it's clearly before the
  open or after the close; otherwise the time isn't confirmed. Estimated dates are marked.
- **Pre-market gaps** (*Gaps* tab, bottom left; `app/gaps.py`): from 04:00 to 10:00 ET on
  weekdays, every minute, the stocks opening well away from yesterday's close (default 3%,
  `gap_min_pct`), biggest first, with why each was scanned: the filing or headline behind it,
  the watchlist, or yesterday's movers. DIL and HALTED badges as everywhere.
  - Yahoo has no pre-market screener, so the scan covers the watchlist, every stock with a
    filing or headline in the last 18 hours, and the last session's movers (at most
    `gap_max_symbols` = 80), in one batched download a minute. A new filing, headline or
    movers list rescans at once.
  - Yahoo's pre-market bars have prices but no volume, so activity is counted in minutes with
    a trade (*Active*); a gap needs `gap_min_minutes` (5) of them, so one stale print doesn't
    count. After 09:30 the gap is measured at the open.
  - A **pre-market brief** goes to the phone at `gap_brief_time` (09:15 ET): the biggest gaps
    with their cause and any high or medium dilution risk. Skipped on mornings without
    pre-market trading. Open `http://localhost:8000/#gaps` to start on the tab.
- **Catalyst reactions** (*Reactions* tab, bottom left; `app/reactions.py`): every filing
  (routine 8-Ks only for watched and moving stocks), new trading halt and alerting headline
  is recorded with the price when it arrived, then 5 and 30 minutes later and at the close
  of the next regular session.
  - The tab shows the median move per catalyst type over 7/30/90 days, with *n* (tracked ·
    measured to the close) and the share that closed lower, and the same for offerings
    grouped by the stock's dilution risk. That's the check on the risk score: if *high* doesn't
    fall harder than *low* after a few weeks, the weights need work. Every offering's risk is
    looked up as it's tracked, so the level already counts that filing.
  - Prices are Yahoo's intraday bars, read once they've arrived (20 minutes after each
    horizon): 1-minute bars with pre/post-market for today, 5-minute bars back to 5 days.
    An event overnight has no 5m/30m reading; it's measured from the last trade before it to
    the next close. Bars come one chart per stock per round, at most 20 a round.
  - Stored in `data/reactions.db` (SQLite, readable by you only): tickers, event types,
    headlines, times and prices. Nothing about you.
  - *Download CSV* saves the tracked catalysts and their moves for the chosen period, for your
    own spreadsheet: time, ticker, catalyst, context, dilution risk, the 5m/30m/close moves and
    the headline. Headlines come from outside feeds, so any starting with = + - @ get a leading
    apostrophe and open as text, never as a formula.
  - *n* is small for weeks; a median of 3 says little.
- **Data health**: the header shows whether every data source is delivering (Yahoo prices,
  Yahoo movers, news feeds, Nasdaq halts, SEC filings, and the Finnhub stream when configured). Click it
  for each source's last update and error.
  - A source is judged by the age of its last good update, not just by errors: a yfinance
    call that hangs never raises an error, so no data for 3 polling intervals (at least 60 s)
    counts as **down**. *Partial* means some data is missing, e.g. one screener failed or a
    ticker got no price.
  - A panel fed by a source that's down gets a red *stale · 4m ago* badge and is dimmed, so
    old numbers don't pass for live ones. When Yahoo fails, the watchlist keeps the last
    prices instead of going blank; a ticker missing from an otherwise good update is dimmed.
  - A news feed is only reported after failing 3 polls in a row: single misses are common
    (PR Newswire sometimes redirects to a URL that 404s for one request).
- The watchlist, keywords and alert settings are saved to `data/state.json`.

## Limitations

- Without Finnhub, prices are **delayed ~15 minutes**. Movers and volume always are. `yfinance`
  is an unofficial scraper that Yahoo sometimes breaks or rate-limits. Use this for personal
  research, not execution.
- "Unusual volume" only scans the watchlist and the ~100 symbols the screeners return,
  not the whole market.
- Press-release wires publish faster than most news sites, but paid feeds (Benzinga,
  Polygon, etc.) are faster still.

## Layout

```
app/
  config.py   settings (config.toml) + persisted watchlist/keywords
  market.py   quotes, screeners, RVOL, unusual-volume filter
  finnhub.py  real-time trade stream + REST quotes, overlaid on Yahoo rows
  news.py     feed polling, ticker extraction, dedupe, keyword alerts
  sec.py      the one client for SEC hosts: rate limit, contact address, ticker map
  filings.py  SEC offerings, Form 4 insider trades, 13D stakes and 8-K items, as news items
  dilution.py dilution risk profile: filing history, cash runway, share growth
  reactions.py  catalyst reaction tracking: what's due, price at each horizon, medians
  gaps.py     pre-market gaps: the gap, what to scan, what to show, the 09:15 brief
  earnings.py next earnings dates: before the open / after the close, brief and recap lines
  recap.py    end-of-day recap message and its schedule
  setup.py    dashboard settings (write-only secrets) and the first-run checklist
  tls.py      HTTPS: a name-constrained local CA and the server certificate it signs
  warrants.py warrant overhang signals from a filing's first pages (sold, outstanding)
  serve.py    python -m app.serve [--https]: starts the app, as the background service does
  clock.py    the market clock: real time, or a replayed day's time and speed
  replay.py   record what the data sources answer, and replay it offline (python -m app.replay)
  sentiment.py  rule-based headline sentiment (finance lexicon + negation)
  alerts.py   price/move/RVOL alert rules (crossing + cooldown) and unusual-volume entries
  health.py   per-source health: last good update, errors, staleness, outage alerts
  halts.py    Nasdaq Trader halts feed: parsing, reason codes, new-halt/resumption diffing
  notify.py   phone push via ntfy / Telegram: batching, rate cap, delivery status
  engine/     the engine: one object, a file per area (see engine/__init__.py)
    core.py       lifecycle, loop runner, health, snapshot, dashboard actions, settings
    prices.py     quotes, Finnhub ticks, movers, unusual volume, price alerts
    feeds.py      news, SEC filings, halts
    risk.py       dilution-risk badges and the level filing alerts carry
    catalysts.py  catalyst reaction tracking and the Reactions report
    daily.py      the end-of-day recap
    gaps.py       the pre-market gap scanner and its brief
    earnings.py   the watchlist's next earnings dates
  main.py     FastAPI routes, access token and /ws endpoint
static/       single-page dashboard: plain scripts sharing globals, no build step, loaded in this order
  core.js     state, formatting, the market clock, server calls
  tables.js   table rows and badges (HALTED, DIL, ER, sentiment): watchlist, movers, unusual
  tabs.js     left-panel tabs: halts, pre-market gaps, catalyst reactions
  news.js     news feed, filters, keyword highlighting
  alerts.js   alert rules and log, the Phone box, desktop notifications and sound
  header.js   data-source health and the session clock
  app.js      server messages, the live connection, sign-in, events, startup
  detail.js   ticker detail drawer and its canvas chart
  setup.js    Setup drawer: checklist and settings form
scripts/
  install-service.sh  systemd user service install/uninstall
tests/        offline (a fixture blocks any network access); scenario_kit.py builds made-up days.
              They use a scratch
              config (MARKET_PULSE_CONFIG) and data folder (MARKET_PULSE_DATA), never yours
```
