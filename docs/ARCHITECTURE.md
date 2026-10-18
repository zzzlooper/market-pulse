# Architecture

Every claim cites `file:line` (paths from the repo root) so it can be checked. **UNVERIFIED** marks
anything inferred rather than read in the code. Line numbers match the commit that last changed
this file.

## 1. Overview

Market Pulse is a single-process FastAPI app. Background loops poll Yahoo, a Finnhub trade stream,
SEC EDGAR, press-wire RSS feeds and the Nasdaq halts feed, and keep a live snapshot of the market
(`app/engine/core.py:160-189`, `app/engine/core.py:130-156`). Changes go to every connected browser
over one WebSocket (`app/engine/core.py:60-74`, `app/main.py:601-618`). Alerts (price rules, unusual
volume, halts, filings, headlines and more) are also pushed to a phone through ntfy and/or Telegram
(`app/notify.py:1-6`, `app/notify.py:217-248`). The dashboard is plain JavaScript with no build step,
served from `static/` (`app/main.py:621`).

## 2. Modules

| Module | Responsibility | Key file:line |
|---|---|---|
| `app/main.py` | HTTP routes, access-token middleware, sign-in, `/ws`, logging setup; builds the Engine at import | `build_engine` `app/main.py:71`, `require_token` `app/main.py:160`, `websocket` `app/main.py:601` |
| `app/engine/` | One `Engine` made from per-area mixins; runs the polling loops and holds the snapshot | `Engine` `app/engine/core.py:77`, mixin list `app/engine/__init__.py:1-17` |
| `app/engine/core.py` | Lifecycle, loop runner, health watch, the alert funnel, user actions, settings | `_loop` `:206`, `_run_step` `:222`, `_route` `:269`, `_publish_alerts` `:283`, `_publish_items` `:309` |
| `app/engine/prices.py` | Watchlist quotes, Finnhub tick flushing, movers, unusual volume | `_update_quotes` `:37`, `_publish_quotes` `:59`, `_update_movers` `:73` |
| `app/engine/feeds.py` | News, filings, halts polling and their alert handoff | `_update_news` `:23`, `_update_filings` `:41`, `_apply_halts` `:76` |
| `app/engine/risk.py` | Dilution-risk summaries; warrant/offering price-level alerts | `_check_levels` `:56`, `_update_risk` `:99` |
| `app/engine/tape.py` | SSR, float rotation, breakouts, LULD near-band warnings, sympathy | `_check_tape` `:123`, `_check_sympathy` `:160`, `_check_luld` `:225` |
| `app/engine/resumptions.py` | Halt-resumption heads-up and "resumed" report | `_update_resumptions` `:37` |
| `app/config.py` | `Settings` (config.toml + dashboard + env), `UserState` (state.json) | `Settings` `:30`, `load_settings` `:95`, `UserState` `:131` |
| `app/setup.py` | Dashboard-editable settings, masking, validation, first-run checklist | `EDITABLE` `:39`, `DashboardSettings` `:160`, `checklist` `:239` |
| `app/market.py` | Yahoo (yfinance) quotes with RVOL, screeners, unusual-volume filter | `fetch_quotes` `:137`, `fetch_movers` `:102`, `find_unusual` `:92` |
| `app/finnhub.py` | Finnhub WebSocket trades + REST quotes, overlay onto Yahoo rows | `FinnhubFeed` `:43`, `overlay` `:94`, `run` `:112` |
| `app/alerts.py` | Price/move/RVOL rules (crossing + cooldown), unusual-volume entries | `AlertMonitor` `:47`, `check_quotes` `:56`, `check_unusual` `:79` |
| `app/noise.py` | Mutes, snoozes, per-kind daily counts | `muted` `:48`, `snoozed` `:53`, `AlertCounts` `:58` |
| `app/notify.py` | Phone push: batching, per-minute cap, delivery status | `PhoneNotifier` `:132`, `run` `:168`, `_allow` `:208` |
| `app/health.py` | Per-source health and outage events | `SourceHealth` `:37`, `state` `:59`, `outages` `:79` |
| `app/news.py` | RSS polling, ticker extraction, dedupe, keyword matching | `NewsFeed` `:81`, `poll` `:106` |
| `app/sec.py` | The only SEC client: host allow-list, rate limit, User-Agent, ticker map | `SecClient` `:32`, `_turn` `:50`, `tickers` `:87` |
| `app/filings.py` | EDGAR feeds (offerings, Form 4, 13D, 8-K) turned into news items | `FilingsFeed` `:321`, `poll` `:342`, `_build` `:415` |
| `app/dilution.py` | Dilution-risk profile from filing history | `DilutionProfiles` `:320` |
| `app/warrants.py` / `app/splits.py` | Warrant/offering prices and reverse splits read from filing text | `detect` `app/warrants.py:81`, `detect` `app/splits.py:84` |
| `app/halts.py` | Nasdaq halts RSS: parsing, reason codes, new/resumption diffing | `parse_halts` `:81`, `HaltFeed` `:181`, `apply` `:200` |
| `app/luld.py` | LULD band estimates and accuracy checks | `Bands` `:55`, `Checks` `:87` |
| `app/ssr.py` | Short-sale-restriction trigger and carry-over | `triggered` `:18`, `carried_over` `:25` |
| `app/breakouts.py` | Pre-market-high breaks, new high of day after a pullback | `Breakouts` `:36` |
| `app/gaps.py` / `app/plan.py` | Pre-market gap scan window; morning game plan | `scanning` `app/gaps.py:24`, `build` `app/plan.py:115` |
| `app/reactions.py` | SQLite store of catalysts and price reactions at 5m/30m/close | `ReactionStore` `:137`, `HORIZONS` `:32` |
| `app/earnings.py` | Next earnings dates, brief/recap lines | `earnings_row` `:28` |
| `app/recap.py` | End-of-day recap text and weekday scheduler | `build_recap` `:30`, `next_run` `:80` |
| `app/sentiment.py` | Rule-based headline sentiment | `score` `:167` |
| `app/backup.py` | Export/restore of setup, never a secret | `export` `:38`, `read` `:99` |
| `app/datastore.py` | "Your data" inventory and deletion | `DELETABLE` `:17` |
| `app/clock.py` | Real or replayed market clock | `Clock` `:15` |
| `app/replay.py` | Record source answers; replay offline | `Recorder` `:234`, `Player` `:331`, `main` `:430` |
| `app/serve.py` | `python -m app.serve [--https]` entry point | `main` `:16` |
| `app/tls.py` | Local CA and server certificate | `ensure` `:139`, `status` `:172` |
| `static/` | Browser UI; WebSocket client and message handler | `handle` `static/app.js:6`, socket `static/app.js:69` |

Note: CLAUDE.md lists the UI as `app/static/`, but it lives at the repo root `static/` (`app/main.py:621`).

## 3. Data flow

### Shared machinery

- **Loop runner.** Each source runs as `Engine._loop`. It calls the step, then sleeps for the interval
  in market time, or until a wake event fires (`app/engine/core.py:206-220`). `_run_step` records
  success or failure in the source's health (`app/engine/core.py:222-234`).
- **Noise controls.** `_route` decides, for each alert, whether to show it in the browser and whether
  to send it to the phone, and counts it (`app/engine/core.py:269-281`):
  1. An alert whose stocks are all snoozed is counted and dropped (`:273-275`).
  2. It goes to the phone only if a channel is configured, its category is on, the kind isn't
     phone-muted, and it is under `phone_cap_per_kind` for the day (`:276-280`).
  3. A browser mute keeps it off the dashboard (`:281`).
- **Alert funnel.** `_publish_alerts` (`app/engine/core.py:283-307`) carries every alert except news
  and filing items:
  1. Track records and reaction tracking, muted alerts included (`:289-290`).
  2. `_route` for each alert (`:293-299`), then counts are saved (`:300`).
  3. Shown alerts go to the alert log and are broadcast as `{"type":"alerts"}` (`:301-304`); phone
     notes are queued (`:305-306`).
- **News and filing items.** `_publish_items` (`app/engine/core.py:309-328`) runs each item flagged
  as an alert through `_route` (`:316-321`, kinds `news` and `filing` at `app/noise.py:43-44`).
  - A muted or snoozed item loses its `alert` flag but is still broadcast to the news panel
    (`:318`, `:321`).
  - Phone notes are queued (`:325-326`).
- **Phone sender.** `PhoneNotifier.run` (`app/notify.py:168-183`) works through the queue:
  - It gathers a burst for `BATCH_WINDOW`=2.0 s (`app/notify.py:23`).
  - It sends at most `PER_BATCH`=3 messages; the rest become one summary (`app/notify.py:24`, `:178`).
  - It drops anything over `PER_MINUTE`=20 in a sliding 60 s window (`app/notify.py:25`, `:208-215`).
  - The queue holds `QUEUE_LIMIT`=500 notes (`app/notify.py:26`, `:163-166`).
  - `enqueue` drops a note whose category is switched off (`app/notify.py:160-162`).
- **Browser.** `/ws` sends a full snapshot on connect (`app/main.py:612`), then every `Hub.broadcast`
  (`app/engine/core.py:69-74`). `handle` dispatches messages by type (`static/app.js:6`). Desktop
  notifications are capped at `MAX_NOTIFICATIONS`=3 per batch (`static/core.js:24`,
  `static/alerts.js:155-164`).

### Yahoo (quotes and movers)

1. **Start.** `start()` launches the quotes loop at `quotes_interval`=15 s (`app/engine/core.py:163`,
   `app/config.py:48`) and the movers loop at `movers_interval`=60 s (`app/engine/core.py:164`,
   `app/config.py:49`).
2. **Fetch.** `_update_quotes` calls `market.fetch_quotes` through `_call` (`app/engine/prices.py:37-39`).
   Live, `_call` runs the blocking yfinance call on a thread (`app/engine/core.py:121-123`,
   `app/replay.py:71-72`).
3. **RVOL.** `fetch_quotes` downloads daily bars (`app/market.py:137-141`). It computes RVOL from the
   mean volume of the previous 10 bars, paced by how far the session has run
   (`app/market.py:153`, `:162`).
4. **Empty or partial answers.** An empty answer raises `SourceError` and the old rows stay on screen
   (`app/engine/prices.py:40-43`). A symbol missing from the answer keeps its previous row, marked
   `stale` (`app/engine/prices.py:44-47`).
5. **Publish.** `_publish_quotes` broadcasts `quotes` (`app/engine/prices.py:67`), then runs three checks:
   - price rules: `alerts.check_quotes` (`app/engine/prices.py:69`);
   - filing price levels: `_check_levels` (`:70`);
   - tape checks: `_check_tape` (`:71`), which also runs sympathy, breakouts and LULD
     (`app/engine/tape.py:155-158`).
6. **Price rules.** A rule fires only on a crossing, when the condition was false at the last check
   (`app/alerts.py:72`). It then waits `COOLDOWN_SECONDS`=1800 per rule+symbol
   (`app/alerts.py:18`, `:74`). The first reading only records a baseline (`app/alerts.py:69-73`).
7. **Movers.** `_update_movers` runs `yf.screen` for each of 4 screens (`app/market.py:28-33`,
   `:105-110`) and broadcasts `movers` (`app/engine/prices.py:81`).
   - Non-watchlist movers get the level and tape checks (`app/engine/prices.py:84-87`).
   - Price rules only ever see watchlist quotes.
8. **Unusual volume.** `_update_unusual` filters quotes and movers by `rvol_threshold`/`min_volume`
   (`app/engine/prices.py:92-95`, `app/market.py:92`). Alerts start only once movers have loaded
   (`app/engine/prices.py:96-99`). `check_unusual` fires once per symbol per day, and the first list of
   each day is a baseline (`app/alerts.py:79-98`).
9. **To alert.** Every alert above goes through `_publish_alerts`.

### Finnhub stream

1. **Created only with a key.** `FinnhubFeed` exists only when `finnhub_api_key` is set. Its update
   callback is `_ticks.set` (`app/engine/core.py:101`, `app/engine/prices.py:24`).
2. **Start.** `start()` subscribes the watchlist, at most `MAX_SYMBOLS`=50 (`app/finnhub.py:24`,
   `:75-92`). It then starts the stream, a REST quote loop at `finnhub_quotes_interval`=300 s, and the
   tick flusher (`app/engine/core.py:182-189`, `app/config.py:35`).
3. **Stream.** `run` connects to `wss://ws.finnhub.io?token=<key>` (`app/finnhub.py:22`, `:117`).
   `handle` stores each trade's price and time, ignoring out-of-order trades, and stamps `seen`
   (`app/finnhub.py:135-154`).
4. **REST.** `refresh_quotes` calls `GET /quote` with the key in the `X-Finnhub-Token` header
   (`app/finnhub.py:54`, `:156-185`). It fills prices before the first trade and keeps the previous
   close current. It runs as the `finnhub_quotes` loop, a health source of its own ("Finnhub
   quotes", `app/engine/core.py:117`, `:186`).
5. **Flush.** `_flush_ticks` wakes on `_ticks` and calls `_publish_quotes(ticks=True)` at most every
   `TICK_FLUSH_SECONDS`=0.5 (`app/engine/prices.py:12`, `:52-57`). It broadcasts only the rows whose
   price moved, as `quote_ticks` (`app/engine/prices.py:63-65`).
6. **Overlay.** The `quotes` property puts Finnhub's price and change on each Yahoo row; volume and
   RVOL stay Yahoo's (`app/engine/prices.py:30-35`, `app/finnhub.py:94-110`). A price counts as fresh
   if it was seen since the current connection opened, or within `STALE_SECONDS`=30
   (`app/finnhub.py:25`, `:60-65`). Otherwise the row keeps Yahoo's price and is marked
   `delayed` (`app/finnhub.py:99-100`).
7. **To alert.** The same checks as Yahoo run on every flush: price rules, levels and tape
   (`app/engine/prices.py:69-71`), then `_publish_alerts`. Unusual volume runs only on the Yahoo path
   (`app/engine/prices.py:49`, `:88`).

### SEC (EDGAR filings)

1. **Start.** The filings loop runs at `filings_interval`=30 s, and only when `sec_filings` is on
   (`app/engine/core.py:89`, `:179-181`, `app/config.py:58-59`).
2. **Client.** Every SEC request goes through `SecClient`:
   - only `www.sec.gov` and `data.sec.gov` are allowed (`app/sec.py:22`, `:50-53`);
   - redirects are not followed (`app/sec.py:34`);
   - request starts are spaced `MIN_REQUEST_GAP`=0.125 s apart (`app/sec.py:24`, `:54-58`);
   - the User-Agent is `sec_user_agent` (`app/sec.py:34`).
3. **Ticker map.** `FilingsFeed.poll` needs the SEC ticker map first (`app/filings.py:345-346`). The
   map is loaded once, and after a failure retried every 600 s (`app/sec.py:25`, `:87-115`).
4. **Feeds.** `poll` reads the feeds for 8 form types (`app/filings.py:41`, `:347`). It pages up to 3
   times while a whole page is new (`app/filings.py:402-413`).
5. **Dedupe and baseline.** Entries are grouped by accession number and skip any already in `_seen`
   (`app/filings.py:359-363`). A filing can alert only if its feed type had been fetched before
   (`app/filings.py:364-365`).
6. **Build.** `_build` classifies the filing (`app/filings.py:415`): offering, Form 4, 13D, or 8-K items.
   It reads the document head for warrant prices or a reverse split (`app/filings.py:491-499`).
7. **Alert flag.**
   - Insider sales are dropped unless the stock is on the watchlist (`app/filings.py:387-388`).
   - `hot` is "watchlist", or the stock's mover reason (`app/filings.py:390-392`,
     `app/engine/prices.py:101-116`).
   - `alert = primed and hot is not None` (`app/filings.py:392`).
8. **Engine.**
   - `_update_filings` attaches a risk level to alerting filings, waiting up to 8 s
     (`app/engine/feeds.py:43-49`, `app/engine/risk.py:91-97`).
   - It badges reverse splits (`app/engine/feeds.py:51-57`) and tracks catalysts
     (`app/engine/feeds.py:58-65`).
   - It hands the items to `_publish_items` as kind `filing` (`app/engine/feeds.py:66-67`). There, the
     noise controls decide each alerting filing's browser flag and phone note (see Shared machinery).
9. **Second path: filing price levels.** Dilution profiles supply warrant/offering prices.
   `_check_levels` alerts when a quote crosses one: the first price is a baseline, and each level and
   direction fires once per day (`app/engine/risk.py:56-89`). These go through `_publish_alerts`.

### Press wires (news RSS)

1. **Start.** The news loop runs at `news_interval`=30 s and wakes early when a symbol is added
   (`app/engine/core.py:165`, `:341`, `app/config.py:50`).
2. **Sources** (`app/news.py:108-112`):
   - the `feeds` setting, by default GlobeNewswire and PR Newswire (`app/config.py:21-24`, `:55`);
   - the SEC 8-K feed, only when `sec_filings` is off (`app/config.py:20`);
   - one Yahoo per-ticker RSS per watchlist symbol when `ticker_news` is on (`app/news.py:24`,
     `app/config.py:56`).
3. **Fetch.** `NewsFeed.poll` fetches all sources in parallel. SEC URLs go through `SecClient`;
   everything else goes through its own client (`app/news.py:113`, `:153-157`).
4. **Items.** `_make_item` extracts tickers from exchange prefixes and cashtags (`app/news.py:30-45`,
   `:159`) and scores sentiment (`app/sentiment.py:167`).
5. **Dedupe.** Items already in `_seen` only merge their tickers (`app/news.py:132-135`). `_seen` is
   capped at 20,000 (`app/news.py:26`, `:144-145`).
6. **Baseline.** The first successful fetch of each feed URL never alerts (`app/news.py:126-127`,
   `:137`).
7. **Alert flag.** `alert = primed and (keyword matched or a ticker is on the watchlist)`
   (`app/news.py:136-137`).
8. **Engine.** `_update_news` tracks alerting headlines with a ticker as catalysts, muted ones
   included (`app/engine/feeds.py:26-29`). It then hands the items to `_publish_items` as kind `news`
   (`app/engine/feeds.py:30`). In the browser, items that keep their `alert` flag beep and raise a
   desktop notification (`static/news.js:85-91`).

### Nasdaq halts

1. **Start.** The halts loop runs at `halts_interval`=20 s (`app/engine/core.py:166`,
   `app/config.py:51`).
2. **Fetch and parse.** `HaltFeed.poll` gets `HALTS_URL` and parses it with `parse_halts`
   (`app/halts.py:19`, `:81-112`, `:195-198`).
3. **Diff.** `apply` treats the first list as a baseline (`app/halts.py:204-207`). After that it
   returns new halts and halts whose resumption time just appeared (`app/halts.py:208-215`).
4. **Engine.** `_apply_halts` broadcasts `halts` on any change and tracks each new halt
   (`app/engine/feeds.py:76-84`). LULD pauses are checked against the band estimate
   (`app/engine/feeds.py:80-81`).
5. **Alerts.** `halt_messages` filters by `halt_alerts` (watchlist/all/off) and produces `halt` and
   `resume` alerts. These go through `_publish_alerts` (`app/halts.py:118-140`,
   `app/engine/feeds.py:85-86`).
6. **Resumptions.** A separate loop runs every `RESUME_TICK`=5 s (`app/engine/resumptions.py:16`,
   `app/engine/core.py:176`):
   - **Heads-up.** It fires `resuming` once when a resumption is ≤60 s away
     (`app/engine/resumptions.py:46-51`).
   - **Resumed report.** It fires `resumed` once it finds the first 1-minute bar. Only halts whose
     resumption was seen still ahead count, so one already past at startup is not reported
     (`app/engine/resumptions.py:47`, `:52-72`).
   - Both go through `_publish_alerts` (`app/engine/resumptions.py:73-74`).

## 4. Failure paths

The shared rule for every loop: `_run_step` catches any exception. It logs a `SourceError` without a
traceback and anything else with `log.exception`, then calls `health.fail`. The loop keeps its normal
interval; there is no backoff (`app/engine/core.py:222-231`). A source goes "down" when its last good
update is older than `max(3 × interval, 60 s)`, whatever the cause, which also catches a hung call
(`app/health.py:3-5`, `:13-14`, `:49`, `:62-63`).

| Failure | How it's detected | What the system does | file:line |
|---|---|---|---|
| Yahoo returns no quotes | `symbols and not rows` | Raises `SourceError`; last rows stay on screen; health records the error and later goes stale/down | `app/engine/prices.py:40-43` |
| Symbol missing from a Yahoo answer | Symbol absent from the returned set | Previous row kept, marked `stale`; health note "no data for …" (degraded) | `app/engine/prices.py:44-50` |
| One Yahoo screener fails | `except Exception` per screen | Logged; that list keeps its previous rows; note "screener failing" | `app/market.py:106-110`, `app/engine/prices.py:77`, `:89-90` |
| All Yahoo screeners fail | `not fetched` | `SourceError("every Yahoo screener failed")` | `app/engine/prices.py:75-76` |
| Yahoo call hangs | No raise; last good update ages past the stale limit | Health reports "down" with "no update in over Ns" | `app/health.py:3-5`, `:62-63`, `:73-74` |
| Ticker panel profile/chart fails | `gather(return_exceptions=True)` | Partial answer listing `errors`; 404 only if there is no data and no error | `app/engine/core.py:516-529`, `app/main.py:415-417` |
| Finnhub stream drops / can't connect | `except Exception` in `run` | Key-redacted `error` set; `connected_at=None`; retry with backoff 1→2→…→60 s, reset on connect; resubscribe all | `app/finnhub.py:114-133` |
| Finnhub price goes stale | `_fresh` false (>30 s and not seen since the current connection) | Row falls back to Yahoo's price, `live: False, delayed: True` | `app/finnhub.py:60-65`, `:99-100` |
| Finnhub stream not connected (health) | `_check_health` every 5 s checks `connected` | `health.fail("finnhub", …)` | `app/engine/core.py:57`, `:243-247` |
| Finnhub error message on stream | `type == "error"` | Logged redacted; becomes the health note while connected | `app/finnhub.py:137-139`, `app/engine/core.py:244-245` |
| Finnhub REST 429 | `status_code == 429` | Rest of this refresh skipped until the next cycle; health note "rate limit hit" (degraded) | `app/finnhub.py:167-170`, `:185` |
| Finnhub key rejected | `status_code in (401, 403)` | `error` names `finnhub_api_key`; `SourceError` marks Finnhub quotes failed | `app/finnhub.py:171-173` |
| Finnhub REST error or unreachable | Non-2xx answer, or `httpx.HTTPError` | `SourceError` (key redacted) marks Finnhub quotes failed; down once stale for 3 refreshes | `app/finnhub.py:164-166`, `:174-175`, `app/engine/core.py:117`, `:230-231` |
| >50 Finnhub symbols | Count > `MAX_SYMBOLS` | Warning; the extra symbols stay on delayed Yahoo prices | `app/finnhub.py:80-81` |
| SEC rejects the User-Agent | HTTP 403 | `SourceError` telling the user to set `sec_user_agent`; startup also warns about the example value | `app/sec.py:62-64`, `app/main.py:100-103` |
| SEC other non-2xx (incl. 429) | `raise_for_status()` | Raised to the loop; no special 429 handling; requests are pre-spaced at 8/s | `app/sec.py:65`, `:24` |
| SEC ticker map unavailable | Download raises | `tickers_error` set, retried after 600 s; `require_tickers` raises so the whole filings poll fails | `app/sec.py:106-108`, `:111-115`, `app/filings.py:346` |
| One EDGAR feed fails | Exception from `_entries` | Logged; failure streak counted; reported as degraded after 3 in a row | `app/filings.py:353-358`, `:339-340`, `app/engine/feeds.py:69-70` |
| All 8 EDGAR feeds fail | `len(failed) == len(FEED_TYPES)` | `SourceError` carrying the first `SourceError` reason (e.g. the 403 advice) | `app/filings.py:366-367`, `:543-546` |
| One filing fails to build | Exception from `_build` | Retried next poll, up to `MAX_ATTEMPTS`=3, then marked seen and dropped | `app/filings.py:374-382` |
| Filing document unreadable | `except Exception` around `get_head` | The item still goes out, without warrant/split details | `app/filings.py:494-498` |
| One news feed fails | Exception from `_fetch` | Logged; streak counted; degraded after `FAILING_AFTER`=3 in a row | `app/news.py:27`, `:120-124`, `:148-151`, `app/engine/feeds.py:35` |
| All news feeds fail | `len(failed) == total` | `SourceError("all N feeds failed")` | `app/engine/feeds.py:33-34` |
| Halts feed HTTP error | `raise_for_status()` | Raised to the loop; previous halt list kept | `app/halts.py:196-198` |
| Halts XML malformed | `ET_XML.fromstring` raises (not caught locally) | `log.exception` plus `health.fail`; previous list kept | `app/halts.py:82`, `app/engine/core.py:225-231` |
| Halt item lacks symbol/time or has a bad pause price | `halted_at is None` / `ValueError` | Item skipped / `pause_price=None` | `app/halts.py:89-90`, `:94-97` |
| Resumption bars not available yet | Exception from `fetch_chart`, or no bar found | Retried every 30 s; gives up after 1800 s | `app/engine/resumptions.py:18-20`, `:54-67` |
| A source down for a long time | `outages(source_alert_minutes*60)` checked every 5 s | One phone alert ("sources") when it goes down and one on recovery; health broadcast on change | `app/engine/core.py:248-257`, `app/health.py:79-97`, `app/notify.py:109-118` |
| Health check itself throws | `except Exception` | Logged; retried in 5 s | `app/engine/core.py:259-265` |
| Phone delivery fails | Non-200 → `RuntimeError`; `gather(return_exceptions=True)` | `last_error` set and broadcast; no retry; Setup checklist warns | `app/notify.py:193-203`, `:231-234`, `:244-248`, `app/setup.py:291-293` |
| Phone alert storm | `_allow()` sliding window | Over 20/min dropped with a warning; bursts over 3 collapsed to a summary | `app/notify.py:178-182`, `:208-215` |
| Phone queue full | `asyncio.QueueFull` at 500 | Note dropped with a warning | `app/notify.py:163-166` |
| Per-kind daily phone cap reached | `alert_counts.phoned(kind, day) >= cap` | Shown in the browser, not sent to the phone; news and filing alerts included | `app/engine/core.py:276-281` |
| Browser socket dead mid-broadcast | `except Exception` on send | Client removed from the hub | `app/engine/core.py:70-74` |
| WebSocket from another site / without token | `same_origin` / `host_allowed` / `authorized` | Closed with 4403 / 4401 | `app/main.py:603-609` |
| HTTP request to non-loopback Host without a token | `host_allowed` false | 403 (DNS-rebinding guard) | `app/main.py:143-148`, `:162-164` |
| Cross-site write request | `Origin` netloc ≠ Host on non-GET | 403 | `app/main.py:167-170` |
| Missing/wrong token on `/api/*` | `authorized` false | 401 | `app/main.py:171-173` |
| Wrong login token | `compare_digest` false | 1.0 s delay, then 401 | `app/main.py:65`, `:186-188` |
| Bad config.toml | Unknown key, non-URL feed, bad `HH:MM` → `ValueError` | Raised from `load_settings`, which runs when `app.main` is imported. **UNVERIFIED**: the process then fails to start | `app/config.py:98-116`, `app/main.py:72`, `:92` |
| Corrupt state.json | `json.loads` is not guarded | **UNVERIFIED**: startup fails the same way | `app/config.py:136` |
| Corrupt settings.json | `FileNotFoundError`/`ValueError` | Silently treated as no dashboard settings | `app/setup.py:167-171` |
| Corrupt alert_counts.json / luld_checks.json | `OSError`/`ValueError` | Treated as empty | `app/noise.py:63-66`, `app/luld.py:95-98` |
| Bad dashboard setting | `validate` raises `SettingsError`, or env-owned setting, or `effective()` fails | 422; nothing saved | `app/engine/core.py:586-597`, `app/main.py:263-266` |
| Bad backup file | >256,000 bytes / not JSON / wrong marker / invalid field | 413 / 422 via `BackupError`; everything is validated before any state changes | `app/main.py:463-473`, `app/backup.py:102-149`, `app/engine/core.py:398-402` |
| TLS address discovery fails | `ip -j addr` raises | Falls back to `getaddrinfo` | `app/tls.py:55-63` |
| TLS cert near expiry / addresses changed | `< RENEW_DAYS`=30 or covered addresses differ | Reissued at the next `--https` start; checklist warns | `app/tls.py:39`, `:159-168`, `app/serve.py:27-30`, `app/setup.py:271-274` |

## 5. State

**Locations.**
- The data folder is `$MARKET_PULSE_DATA` or `<repo>/data` (`app/config.py:15`).
- The config file is `$MARKET_PULSE_CONFIG` or `<repo>/config.toml` (`app/config.py:13`).
- Both `data/` and `config.toml` are gitignored (`.gitignore:4-5`).
- Tests point both env vars at temp folders before importing the app (`tests/conftest.py:8-11`).

### Persisted (survives a restart)

| What | Where / format | Written by | Notes | file:line |
|---|---|---|---|---|
| Watchlist, keywords, alert rules, alert switches, mutes, snoozes, halt mode, phone categories, the days the last recap and brief went out | `data/state.json`, JSON | `UserState.save` on every UI change via `_config_changed`, and after each recap and brief | Atomic tmp+replace, created 0600 | `app/config.py:131-176`, `app/engine/core.py:482-484` |
| Dashboard setting overrides, **including secrets entered in Setup** (plain text) | `data/settings.json`, JSON | `DashboardSettings.save` from `update_settings` | Created 0600, atomic; only `EDITABLE` names are loaded | `app/setup.py:167-182`, `app/engine/core.py:597`, `app/main.py:72` |
| Alerts fired/phoned per kind per day, last 8 days | `data/alert_counts.json`, JSON | `AlertCounts.save` on every publish | So the daily phone cap survives a restart; written then chmod 0600 | `app/noise.py:21`, `:88-96`, `app/engine/core.py:104`, `:276-280`, `:300`, `:325` |
| LULD estimate-vs-official checks, last 500 | `data/luld_checks.json`, JSON | `Checks.add` | Written then chmod 0600; no tickers | `app/luld.py:84-108`, `app/engine/tape.py:63` |
| Catalyst events and their 5m/30m/close reactions | `data/reactions.db`, SQLite `events` table | `ReactionStore` | `INSERT OR IGNORE` so a restart doesn't re-track; chmod 0600 when created; pruned daily by `reactions_keep_days`; `VACUUM` after clearing | `app/reactions.py:34-51`, `:141-167`, `:194-213`, `app/engine/catalysts.py:70`, `:108-116` |
| Recordings of source answers | `data/recordings/*.jsonl.gz` | `Recorder` when `record` or `MARKET_PULSE_RECORD=1` | Folder 0700, files 0600 `O_EXCL`; newest `record_keep` kept; no request headers; credential-like URL params dropped | `app/main.py:85-88`, `app/replay.py:11-13`, `:57`, `:80-84`, `:241-250`, `:312-316` |
| Local CA and server certificate | `data/tls/` | `tls.ensure` at `--https` start | Folder 0700; keys 0600, certs 0644 | `app/config.py:16`, `app/tls.py:76-80`, `:139-169`, `app/serve.py:26-30` |

The "Your data" panel lists these stores (`app/engine/core.py:546-558`). Its delete action covers
`reactions`, `recordings`, `dashboard_settings`, `alert_counts` and `luld_checks`
(`app/datastore.py:17`, `app/engine/core.py:560-578`).

### In memory only (rebuilt after a restart)

Rebuilding these is what makes "a restart only sets a baseline" true.
- **Alerts:** rule baselines and cooldowns (`app/alerts.py:50-51`), the unusual-volume seen-set
  (`app/alerts.py:52-53`), and the last 100 alerts shown (`app/alerts.py:19`, `:54`).
- **News:** `_items`, `_seen` and `_primed` (`app/news.py:89-91`).
- **Filings:** `_seen`, `_primed`, `_attempts` and `_was_primed` (`app/filings.py:327-333`).
- **Halts:** the halt list and its primed flag (`app/halts.py:184-185`).
- **Resumptions:** warned, done and ahead sets (`app/engine/resumptions.py:30-35`).
- **Filing levels:** last price and fired-today (`app/engine/risk.py:29-30`).
- **Tape state:** SSR, floats, rotation, breakouts, LULD bands/cooldowns and sympathy
  (`app/engine/tape.py:42-64`).
- **Health**, including the outage-alerted flag (`app/health.py:25-40`).
- **Finnhub prices** (`app/finnhub.py:48`).
- **Phone queue**, rate window and last sent/error (`app/notify.py:138-141`).
- **SEC ticker map** (`app/sec.py:38-42`) and **dilution profile cache** (`app/dilution.py:320`).
- **Catalyst helpers:** the offerings waiting for a risk level (`app/engine/catalysts.py:72`).
  **UNVERIFIED**: offerings queued at shutdown never get one.

**Missed scheduled sends.** The recap and the brief handle a restart differently.
- **Recap.** The day the last recap went out is saved as `recap_sent` in `state.json`
  (`app/config.py:149`, `:164`). At startup, `_recap_missed` checks three things: it's a weekday,
  `recap_time` has passed, and today's recap hasn't gone out (`app/engine/daily.py:39-45`). If all
  hold, the loop waits `RECAP_CATCH_UP`=60 s for the first quotes, then sends the missed recap
  (`app/engine/daily.py:15`, `:61-64`). After that it sleeps until `next_run`, which is always after
  now (`app/engine/daily.py:65-67`, `app/recap.py:85-86`).
- **Marking.** `_send_recap` marks the day only after a success, so a failed recap is tried again on
  the next restart that day (`app/engine/daily.py:55-59`).
- **Brief.** The pre-market brief works the same way, with `brief_sent` (`app/config.py:150`,
  `:165`), plus a deadline: the market open. `_brief_missed` holds only from `gap_brief_time` until
  09:30 ET (`app/engine/gaps.py:97-105`). The loop waits `BRIEF_CATCH_UP`=60 s for the first scan,
  then checks the deadline again before sending (`app/engine/gaps.py:19`, `:113-117`).
  - `_send_gap_brief` marks the day done whether or not there was pre-market trading
    (`app/engine/gaps.py:124-137`). A brief that raises is left unmarked (`app/engine/gaps.py:107-111`).
  - A restart after the open skips that day's brief: by then it's old news.

**Replay never touches real data.** A replay runs with state, settings and reactions in a temp
folder, deleted on shutdown (`app/main.py:74-84`, `:112-113`). It also clears the phone and Finnhub
credentials (`app/replay.py:415-418`). Tested in `tests/test_replay.py:368`.

## 6. Config and secrets

### Loading

1. **Layers.** Defaults (`app/config.py:30-82`), then `config.toml`, then `data/settings.json` from the
   dashboard, then environment variables (`app/config.py:95-104`, `app/setup.py:3-4`).
2. **Load time.** Settings load once, when `app.main` is imported (`app/main.py:72`, `:92`).
3. **Environment variables.** Only the `SECRET_ENV` names can come from the environment, as
   upper-case variables (`app/config.py:85-88`, `:102-104`). They are `sec_user_agent`,
   `finnhub_api_key`, `ntfy_topic`, `ntfy_token`, `telegram_bot_token`, `telegram_chat_id` and
   `access_token`.
4. **Locked settings.** A setting set by the environment shows as locked and can't be changed from the
   dashboard (`app/setup.py:184-186`, `:212`, `app/engine/core.py:587-588`).
5. **Validation.**
   - Unknown keys in config.toml are rejected (`app/config.py:98-100`).
   - A malformed feed URL is reported without echoing it, since it may hold a token
     (`app/config.py:108-113`).
   - Dashboard values are validated, and the error messages never repeat the value
     (`app/setup.py:103-144`).
6. **Live changes.** Dashboard changes apply live, except `finnhub_api_key` and `record`, which need a
   restart (`app/setup.py:50`, `:70`, `:223-232`).

### Keeping secrets out of output

- **Masked in Setup.** Fields of kind `secret`/`email` are write-only (`app/setup.py:6-11`,
  `:197-216`).
  - The API returns only `masked()`: the last 3 characters for values of 12+ characters, otherwise
    "set". The SEC email shows as `a…@….com` (`app/setup.py:147-157`).
  - A generated access token or ntfy topic is returned once (`app/main.py:270-281`).
  - Settings responses are `Cache-Control: no-store` (`app/main.py:244`).
  - Tests: `tests/test_setup.py:63`, `:87`.
- **Phone status masked.** It shows only the last 4 characters of the ntfy topic and chat id
  (`app/notify.py:128-129`, `:148-155`).
- **Phone errors.** They are built from the status code and response body, never the request URL:
  - the ntfy topic and the Telegram bot token both sit in the URL (`app/notify.py:229-234`,
    `:239-248`);
  - test: `tests/test_notify.py:86`.
  - **UNVERIFIED:** whether httpx transport errors (connect or timeout) could include the URL in the
    `last_error` text.
- **Left out of backups.** `secret`/`email` kinds and `public_url` are never exported
  (`app/backup.py:25`, `:33-35`, `:45-46`). On restore they are skipped with a note
  (`app/backup.py:140-141`). Tests: `tests/test_backup_noise.py:11`, `:25`.
- **Logging.**
  - Root logging is INFO (`app/main.py:53`).
  - The `httpx` logger is raised to WARNING, because its request lines would carry the Telegram token
    and ntfy topic (`app/main.py:54-56`).
  - `yfinance` is raised to CRITICAL (`app/main.py:58`).
  - The service runs uvicorn with `access_log=False` (`app/serve.py:34`).
  - Setting changes log names only (`app/engine/core.py:601`).
  - A restore logs only the count of changes (`app/engine/core.py:408`), and data deletion only the
    store name (`app/engine/core.py:578`).
  - The SEC example-value warning doesn't print the value (`app/main.py:100-103`).
  - A failed news feed is logged without its address (`app/news.py:121`; see News feed addresses).
- **Finnhub key.**
  - The WebSocket URL carries `?token=<key>` (`app/finnhub.py:117`). REST sends it as a header
    instead (`app/finnhub.py:54`).
  - `_redact` replaces the key with "…" (`app/finnhub.py:67-70`). It is applied to every stream log
    line and to `feed.error` (`app/finnhub.py:127-128`, `:138-139`, `:191`).
  - Tests: `tests/test_finnhub.py:178` (`error`), `:197` (logs).
- **Recordings.** Recordings never hold request headers. Credential-like query parameters are
  stripped from recorded URLs (`app/replay.py:11-13`, `:57`, `:80-84`). Tested in
  `tests/test_replay.py:138` and `:154`.
- **Files on disk.**
  - `state.json`, `settings.json`, the TLS keys and recordings are created 0600 (references in §5).
  - `alert_counts.json` and `luld_checks.json` are written with `write_text` and then chmod-ed, so for
    a moment they have the umask's mode. Neither holds a secret (`app/noise.py:94-95`,
    `app/luld.py:105-106`).

### Access token and sign-in

- **Where it's required.** With `access_token` set, every `/api/*` route except `/api/login`, and
  `/ws`, needs the token. It is accepted from the `mp_token` cookie or an `Authorization: Bearer`
  header, compared with `secrets.compare_digest` (`app/main.py:151-157`, `:171-173`, `:607-609`).
- **Without a token.** The app answers only requests whose Host is loopback (`app/main.py:143-148`).
  The service install script refuses `MP_HOST=0.0.0.0` without a token
  (`scripts/install-service.sh:89-95`).
- **Login.** `/api/login` compares with `compare_digest` and sleeps 1 s on failure
  (`app/main.py:181-188`). It then sets the cookie: HttpOnly, SameSite=strict, Secure under HTTPS,
  365 days (`app/main.py:189-191`).
  - The cookie's value is the token itself, not a session id (`app/main.py:190`).
  - Changing the token re-issues the cookie (`app/main.py:247-253`).
- **Tests:** `tests/test_auth.py:25`, `:39`, `:53`, `:67`; `tests/test_setup.py:150`.

### News feed addresses

A feed URL may hold a token (`app/config.py:110-111`). httpx puts the full URL, query string
included, in its error text: an `HTTPStatusError` from `raise_for_status()` (`app/news.py:156`)
reads "… for url 'https://…?token=…'".
- `poll` logs a failed feed through `feed_error` (`app/news.py:121`). That gives an HTTP error as
  its status code. In any other error text, each address is cut down to its host, which drops the
  query, the path and any `user:password@` (`app/news.py:38`, `:56-68`).
- The health reason names feeds only (`app/engine/feeds.py:33-35`).
- Tests: `tests/test_news.py:35`, `:46`.
