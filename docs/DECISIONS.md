# Design decisions

Ten decisions that shape how Market Pulse behaves. The first five are the invariants in
`CLAUDE.md`. Line numbers are from commit `ff2a741`. "Introduced" is the commit that first added
the behaviour, found with `git log -S` and `git blame`. When a later commit moved or widened it,
that commit is listed too.

## 1. A restart only sets a baseline; alerts never fire on startup

- **Decision:** The first reading for each rule and symbol (and the first list of unusual-volume names) is stored as a baseline. An alert fires only when the condition goes from false to true after that.
- **Alternative not taken:** Fire whenever a condition holds at a check, so a restart or a new rule alerts on everything already true.
- **Trade-off:** A restart or a new rule never floods the phone. In exchange, a condition that became true while the app was down is never reported. You only hear about it if it goes false and then true again.
- **Where:** `app/alerts.py:72` (`crossed = hit and self._hit.get(key) is False`), `app/alerts.py:83-86` (unusual-volume baseline)
- **Introduced:** `1d8ab37` Market Pulse v0.1 baseline. Later widened to sympathy moves by `2fead3a` "Sympathy alerts start from a baseline after a restart" and `39befd1` "Sympathy alerts wait for the leader's baseline too".
- **Why I kept it:**

## 2. Each alert type has its own repeat rule

- **Decision:** Price rules fire on a crossing and then wait 30 minutes per rule and symbol. Other kinds have their own rules: new high of day needs a pullback and then waits 15 minutes, while unusual volume, rotation and sympathy fire once per stock per day.
- **Alternative not taken:** One global cooldown or dedupe window for every alert kind.
- **Trade-off:** Each kind repeats at a rate that suits its signal. A price hovering around a level doesn't alert on every tick, and a once-a-day event isn't sent twice. The cost is more state and more rules to remember: every new alert type needs its own repeat policy and its own tests.
- **Where:** `app/alerts.py:18` (`COOLDOWN_SECONDS = 30 * 60`), `app/alerts.py:74`, `app/breakouts.py:20` (`HOD_COOLDOWN = 900`)
- **Introduced:** `1d8ab37` Market Pulse v0.1 baseline (price-rule cooldown and once-per-day unusual volume). The other kinds brought their own rules as they were added, e.g. `4d3874f` "v0.20: breakout alerts, click the chart to set an alert".
- **Why I kept it:**

## 3. Finnhub falls back to Yahoo's delayed price; Yahoo keeps its last good rows

- **Decision:** A Finnhub price counts as live while the stream that delivered it is still up, however old the trade. Once the stream is down and the price is over 30 s old, the row shows Yahoo's price marked `delayed`. If Yahoo returns nothing, the last good rows stay on screen and are marked `stale`.
- **Alternative not taken:** Expire every Finnhub price after a fixed age, even with the stream up. Or blank the row (or the whole watchlist) when a source goes quiet.
- **Trade-off:** A thinly traded stock keeps its true last trade instead of flipping to an older delayed quote, and the screen never goes empty. The cost is that a `delayed` or `stale` row can sit there looking fairly normal. The row marker and the data health panel are the only signs that a number is old.
- **Where:** `app/finnhub.py:25` (`STALE_SECONDS = 30`), `app/finnhub.py:60-65` (`_fresh`), `app/engine/prices.py:42-47` (Yahoo keeps its last rows)
- **Introduced:** `1c84606` "Stale Finnhub prices fall back to Yahoo's delayed price". Yahoo's keep-last-rows behaviour dates from `1d8ab37` Market Pulse v0.1 baseline.
- **Why I kept it:**

## 4. Phone notifications are capped per minute, batched per burst, and capped per kind per day

- **Decision:** Alerts that arrive within 2 s of each other are sent together: the first two go out one by one and the rest become one summary message. There is a hard cap of 20 phone sends a minute and an optional per-kind daily cap. All alerts, news and filings included, go through one routing function. Desktop notifications show at most 3 per batch.
- **Alternative not taken:** Send every alert to the phone as it fires, or limit only by mutes the user sets by hand.
- **Trade-off:** A market-wide event can't flood the phone or exhaust ntfy/Telegram rate limits. But past the cap, alerts are dropped from the phone (logged, still shown in the browser), so a real signal can be lost on a busy day.
- **Where:** `app/notify.py:23-25` (`BATCH_WINDOW`, `PER_BATCH`, `PER_MINUTE`), `app/notify.py:208-215` (`_allow`), `app/engine/core.py:269-281` (`_route`, `phone_cap_per_kind`), `static/core.js:24` (`MAX_NOTIFICATIONS = 3`)
- **Introduced:** `1d8ab37` Market Pulse v0.1 baseline (per-minute cap, batching, desktop limit). Per-kind daily cap: `c4cb971` "v0.25: alert noise control, settings backup and restore". Routing for news and filings moved into `_route` in `ffce195` "News and filing alerts obey mutes, snoozes and the phone cap".
- **Why I kept it:**

## 5. Tokens and secrets never reach logs or output

- **Decision:** Secrets can be written in Setup but never read back: the API returns at most a few masked characters. Secrets are left out of backups, and only setting names are logged. The Finnhub key is removed from every log line and every dashboard message, because the stream URL contains it.
- **Alternative not taken:** Rely on not logging sensitive values in the first place, or redact only in the UI.
- **Trade-off:** A leaked log or a shared backup holds no credentials. In exchange, a secret can't be checked or recovered from the dashboard (it can only be replaced), and some debugging information is lost.
- **Where:** `app/finnhub.py:67-70` (`_redact`), `app/finnhub.py:127-128`, `app/setup.py:147` (`masked`)
- **Introduced:** Finnhub redaction in the UI: `1d8ab37` Market Pulse v0.1 baseline. Extended to every log line in `286ca6f` "Finnhub errors keep the API key out of every log line, not just the UI". Write-only, masked secrets: `5c02fa1` "v0.6: settings panel and first-run checklist".
- **Why I kept it:**

## 6. One Engine assembled from a mixin per area

- **Decision:** The engine is a single `Engine` class made from mixins (prices, feeds, risk, catalysts, daily, gaps, earnings, tape, resumptions). Each mixin sets up its own state in an `_init_*` method.
- **Alternative not taken:** Separate service objects that are injected and talk through interfaces, or keeping the single 660-line `app/engine.py`.
- **Trade-off:** Each file covers one area, and the split didn't change any method, attribute or patch path. But all mixins share one `self` namespace, so dependencies between them are implicit and attribute names can collide.
- **Where:** `app/engine/core.py:77` (`class Engine(PricesMixin, FeedsMixin, …)`), `app/engine/__init__.py:1-16`
- **Introduced:** `b296aed` "v0.8: split the engine into a package, lint cleanup"
- **Why I kept it:**

## 7. One shared market clock that a replay can drive

- **Decision:** Everything that asks "what time is it in the market?" (sessions, today, cooldowns, reaction horizons, the recap) reads `app.clock.clock`. During a replay it returns the recorded day's time at a chosen speed. Real-time timers (rate limits, caches, health) keep using `time.monotonic`.
- **Alternative not taken:** Call `datetime.now()` directly and test with time-mocking libraries, or have no offline replay.
- **Trade-off:** A recorded day can be replayed offline at 10× with the same alerts and cooldowns, and tests can freeze time. The cost is a rule every module has to follow: one stray `datetime.now()` breaks replays without any error, and each timer has to be put on the right clock.
- **Where:** `app/clock.py:1-7`, `app/clock.py:57` (`clock = Clock()`)
- **Introduced:** `5e99eda` "v0.7: record and replay"
- **Why I kept it:**

## 8. HTTPS through a local CA that is name-constrained to private addresses

- **Decision:** For HTTPS, the app creates its own CA once. The CA is name-constrained to `localhost` and private IP ranges, and it signs a server certificate for this computer's private addresses. You install the CA on the phone once.
- **Alternative not taken:** A plain self-signed certificate, a public certificate (Let's Encrypt and a domain), or a tunnel service.
- **Trade-off:** You get encrypted local access without a domain or a third party. If the CA key leaked, it couldn't be used to impersonate public sites. The cost is a manual CA install on each phone, and Market Pulse has to manage certificate renewal itself.
- **Where:** `app/tls.py:92-93` (`NameConstraints(permitted_subtrees=…)`)
- **Introduced:** `514c68a` "v0.16: HTTPS with Market Pulse's own certificate"
- **Why I kept it:**

## 9. LULD bands estimated from prices the app has seen

- **Decision:** LULD bands are worked out from the plan's rules (as amended in 2020) around the average of the prices the app saw in the last 5 minutes. Tier 1 is guessed from market cap.
- **Alternative not taken:** Use official LULD band data from a paid market-data feed.
- **Trade-off:** It's free and works with the existing sources. But it's an estimate: few readings with Yahoo, a guessed tier, and with delayed prices a warning can come after the pause. Estimates are logged against official bands so their accuracy can be checked.
- **Where:** `app/luld.py:1-18`, `app/luld.py:31` (`WINDOW = 300`)
- **Introduced:** `95d69dd` "v0.23: LULD band estimate, track records in alerts"
- **Why I kept it:**

## 10. One HTTP client for every SEC request

- **Decision:** All SEC traffic (news, filings, dilution profiles) goes through `app/sec.py`. It is the only place that holds the personal contact address SEC requires in the User-Agent. It talks only to SEC hosts, refuses redirects, never logs the address, and spaces requests to at most 8 a second.
- **Alternative not taken:** Each module makes its own httpx calls to EDGAR with the header attached.
- **Trade-off:** The contact address can't be sent to another host, and the app stays under SEC's fair-access limit across every feature. The cost is that all SEC features share one queue, so a slow dilution scan delays filings. And anything a redirect would have reached is refused, not followed.
- **Where:** `app/sec.py:1-6`, `app/sec.py:24` (`MIN_REQUEST_GAP`), `app/sec.py:34` (`follow_redirects=False`)
- **Introduced:** `61f3f05` "v0.3: dilution risk panel and 8-K item alerts" (as `app/sec.py`). SEC requests without redirects started in `dbfa98b` "v0.2: SEC dilution, insider and 13D filings alerts".
- **Why I kept it:**
