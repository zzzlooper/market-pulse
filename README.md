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
cp config.example.toml config.toml   # then set sec_user_agent to your name + email
uvicorn app.main:app --reload
```

Open http://localhost:8000 and click **Enable alerts** to get desktop notifications and a sound.

Tests: `pip install pytest && pytest`

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
`NTFY_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (and `FINNHUB_API_KEY`, `ACCESS_TOKEN`).

- The *Phone* box has switches for which alerts go to the phone: price, halts, news (keyword
  and watchlist headlines), unusual volume and data outages. Unusual volume is off by default,
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
- It's plain HTTP: anyone who can watch your Wi-Fi traffic can see the token. Use it on a home
  network, or put it behind HTTPS (a reverse proxy, or Tailscale) to go further.

## What's on screen

| Panel | Source | Refresh |
|---|---|---|
| Watchlist: price, % change, volume, RVOL | Yahoo daily bars (`yfinance.download`); prices from Finnhub if configured | 15 s (Finnhub: streaming) |
| Top movers: gainers, losers, most active, small caps | Yahoo predefined screeners | 60 s |
| Unusual volume | Watchlist plus every screener result with RVOL ≥ threshold | with the above |
| News | SEC 8-K filings, GlobeNewswire, PR Newswire, Yahoo per-ticker RSS | 30 s |

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
- **Data health**: the header shows whether every data source is delivering (Yahoo prices,
  Yahoo movers, news feeds, Nasdaq halts, and the Finnhub stream when configured). Click it
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
  sentiment.py  rule-based headline sentiment (finance lexicon + negation)
  alerts.py   price/move/RVOL alert rules (crossing + cooldown) and unusual-volume entries
  health.py   per-source health: last good update, errors, staleness, outage alerts
  halts.py    Nasdaq Trader halts feed: parsing, reason codes, new-halt/resumption diffing
  notify.py   phone push via ntfy / Telegram: batching, rate cap, delivery status
  engine.py   background loops + WebSocket broadcast hub
  main.py     FastAPI routes, access token and /ws endpoint
static/       single-page dashboard (vanilla JS, no build step)
  app.js      tables, news, alerts, halts, WebSocket client
  detail.js   ticker detail drawer and its canvas chart
tests/
```
