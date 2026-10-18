# CLAUDE.md
## How to run and test
- Setup (Python 3.11+): `python -m venv .venv && source .venv/bin/activate.fish && pip install -r requirements-dev.txt`
- Run: `uvicorn app.main:app --reload`, then open http://localhost:8000 (as the service does: `python -m app.serve [--https]`)
- Test everything: `python -m pytest` (~430 tests, offline, about 20 s; browser tests skip without Chromium)
- One file or test: `python -m pytest tests/test_alerts.py -q`, `python -m pytest -k crossing`
- Replay a recorded day offline: `python -m app.replay list`, then `python -m app.replay play <file> --speed 10` (port 8001)
- Tests use a scratch config and data folder (MARKET_PULSE_CONFIG, MARKET_PULSE_DATA) and a fixture blocks the network.

## Module map (app/)
- `main.py` FastAPI routes, access token and sign-in, the /ws endpoint, logging setup
- `engine/` the Engine: polling loops and live snapshot, one mixin per area (see `engine/__init__.py`)
- `config.py` settings from config.toml/env/dashboard, plus the persisted watchlist, keywords and rules
- `setup.py` dashboard settings (write-only secrets, masking, validation) and the first-run checklist
- `market.py` Yahoo quotes, screeners, RVOL, unusual-volume filter, charts, profiles
- `finnhub.py` real-time trade stream and REST quotes, overlaid on Yahoo rows
- `alerts.py` price/move/RVOL rules (crossing + cooldown) and unusual-volume entries
- `noise.py` muted kinds, snoozed stocks, the per-kind daily phone cap, alert counts
- `notify.py` ntfy/Telegram push: batching, per-minute rate cap, delivery status
- `health.py` per-source health: last good update, errors, staleness, outage alerts
- `news.py` feed polling, ticker extraction, dedupe, keyword alerts
- `sec.py` the only SEC client: rate limit, contact address, ticker map
- `filings.py` SEC offerings, Form 4, 13D and 8-K items as news items
- `dilution.py` dilution risk profile: filing history, cash runway, share growth
- `warrants.py` warrant overhang and offering price read from a filing
- `splits.py` reverse splits from an 8-K: ratio and effective day
- `halts.py` Nasdaq halts feed: parsing, reason codes, new-halt/resumption diffing
- `luld.py` limit up/limit down bands estimated from the last 5 minutes of prices
- `ssr.py` short sale restriction (Rule 201): triggered today or carried over
- `breakouts.py` pre-market-high breaks and new highs of day after a pullback
- `gaps.py` pre-market gaps: what to scan and show, the 09:15 brief
- `plan.py` the morning game plan: today's gappers with everything known about each
- `reactions.py` catalyst reaction tracking: what's due, price at each horizon, medians
- `earnings.py` next earnings dates, plus brief and recap lines
- `recap.py` end-of-day recap message and the weekday scheduler (`next_run`)
- `sentiment.py` rule-based headline sentiment
- `backup.py` export/restore of the user's setup, never a secret
- `datastore.py` the "Your data" inventory and deletion of stored files
- `clock.py` the market clock: real time, or a replayed day's time and speed
- `replay.py` record data-source answers and replay them offline
- `serve.py` `python -m app.serve` entry point used by the background service
- `tls.py` the local name-constrained CA and server certificate for HTTPS
- `static/` plain-script single-page dashboard with no build step; `tests/` offline, `scenario_kit.py` builds made-up days

## Invariants that must never break
- A restart only sets a baseline; alerts never fire on startup. The first reading or list is a baseline in alerts.py, news.py, filings.py, halts.py, breakouts.py, risk levels, rotation, resumptions and sympathy (`engine/tape.py` `_check_sympathy`: per stock, at its first check once its industry is known). TODO(verify): if a follower's industry loads before its leader's, it can still alert once the leader's arrives.
- Alerts fire on crossings, with a cooldown per alert. Price rules: 30 min per rule+symbol (`alerts.py` COOLDOWN_SECONDS); new high of day and LULD: 15 min; levels, PMH, rotation, unusual, sympathy: once per stock per day. TODO(verify): halts, news and filings fire on new items rather than crossings.
- Every price source has a fallback; live stream failure falls back to delayed data. TODO(verify): Yahoo has no fallback, only last good rows marked `stale`. When the Finnhub stream drops, `FinnhubFeed.overlay` shows Yahoo's delayed price (row marked `delayed`) once a symbol's last Finnhub price is over `STALE_SECONDS`=30 old, and switches back on the next fresh trade or REST quote. While the stream is up, a price received since it connected stays live however old the trade.
- Notifications are rate-capped: phone at PER_MINUTE=20 plus burst batching (`notify.py`) and `phone_cap_per_kind` per day (`engine/core.py` `_publish_alerts`). Desktop notifications collapse each batch to MAX_NOTIFICATIONS (`static/alerts.js`).
- Tokens and secrets never reach logs or output: masked in Setup, left out of backups, httpx logging silenced, only setting names logged. TODO(verify): `finnhub.py` `run()` logs the raw exception, and an invalid URL makes websockets echo `?token=<key>`. The test only checks the UI copy (`feed.error`).

## Rules for agents
- Keep diffs small: one concern per change, and match the surrounding style and comment density.
- Run `python -m pytest` after every change and fix failures before moving on.
- Never read or edit `config.toml`: it holds the user's secrets. Use `config.example.toml` for the shape of the settings.
