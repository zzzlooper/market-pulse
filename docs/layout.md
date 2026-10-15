# Code layout

Where each part lives. Back to the [README](../README.md).


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
  warrants.py warrant overhang signals and the offering price from a filing's first pages
  ssr.py      the short sale restriction (Rule 201): triggered today, carried over from yesterday
  luld.py     limit up / limit down bands, estimated from the last 5 minutes of prices
  splits.py   reverse splits from an 8-K's first pages: the ratio and the day it takes effect
  noise.py    alert noise control: muted kinds, snoozed stocks, the phone cap, daily counts
  backup.py   backing up your setup to a file and restoring it (never a secret)
  plan.py     the morning game plan: today's gappers with everything known about each
  breakouts.py  through the pre-market high, a new high of day after a pullback
  serve.py    python -m app.serve [--https]: starts the app, as the background service does
  clock.py    the market clock: real time, or a replayed day's time and speed
  replay.py   record what the data sources answer, and replay it offline (python -m app.replay)
  sentiment.py  rule-based headline sentiment (finance lexicon + negation)
  alerts.py   price/move/RVOL alert rules (crossing + cooldown) and unusual-volume entries
  health.py   per-source health: last good update, errors, staleness, outage alerts
  halts.py    Nasdaq Trader halts feed: parsing, reason codes, new-halt/resumption diffing, how trading resumed
  notify.py   phone push via ntfy / Telegram: batching, rate cap, delivery status
  engine/     the engine: one object, a file per area (see engine/__init__.py)
    core.py       lifecycle, loop runner, health, snapshot, dashboard actions, settings
    prices.py     quotes, Finnhub ticks, movers, unusual volume, price alerts
    feeds.py      news, SEC filings, halts
    risk.py       dilution-risk badges, the level filing alerts carry, warrant and offering price crossings
    catalysts.py  catalyst and alert reaction tracking, and the Reactions report
    daily.py      the end-of-day recap
    gaps.py       the pre-market gap scanner and its brief
    earnings.py   the watchlist's next earnings dates
    tape.py       SSR, float rotation, breakouts, day-2 runners, LULD bands and sympathy moves
    resumptions.py  halt resumptions: the heads-up a minute before, and how trading resumed
  main.py     FastAPI routes, access token and /ws endpoint
static/       single-page dashboard: plain scripts sharing globals, no build step, loaded in this order
  core.js     state, formatting, the market clock, server calls
  tables.js   table rows and badges (HALTED, DIL, WAR, SSR, ROT, RS, D2, SYM, LULD, ER, sentiment): watchlist, movers, unusual
  tabs.js     left-panel tabs: halts, pre-market gaps, catalyst reactions
  news.js     news feed, filters, keyword highlighting
  alerts.js   alert rules and log, the Phone box, desktop notifications and sound
  header.js   data-source health and the session clock
  app.js      server messages, the live connection, sign-in, events, startup
  detail.js   ticker detail drawer and its canvas chart (levels, click to set an alert)
  setup.js    Setup drawer: checklist and settings form
  plan.js     the game plan drawer
scripts/
  install-service.sh  systemd user service install/uninstall
  screenshots.py      README screenshots from the browser tests' made-up morning
docs/         these pages; README.md is the start page
tests/        offline (a fixture blocks any network access); scenario_kit.py builds made-up days.
              They use a scratch
              config (MARKET_PULSE_CONFIG) and data folder (MARKET_PULSE_DATA), never yours
```
