# Setting up Market Pulse

Settings and privacy, real-time prices, phone alerts, running it in the background,
record and replay, opening it from your phone, and HTTPS. Back to the [README](../README.md).

## Settings and privacy

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
- **Signal thresholds** tune the signals: the pullback before a new high of day counts
  (`breakout_pullback_pct`, 5), the gain that makes day 2 (`day2_min_pct`, 30), float rotation
  (`rotation_multiple`, 1×), how near a LULD band warns (`luld_near_pct`, 25% of the band), when
  a stock leads its industry and when its peers move in sympathy (`sympathy_lead_pct`, 30;
  `sympathy_min_pct`, 5), and how many measured alerts before a track record is quoted
  (`record_min`, 10). Each is checked against a sensible range. The track records in the
  Reactions tab show which settings give alerts worth acting on.
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
(by name only), the reaction history, recordings, the alert counts, the LULD estimate checks,
where the log goes, and what
this browser stored. Paths are shown relative to the project, never in full (that would show your user
name).

- *Delete* clears the reaction history, the recordings (except one being written), the alert
  counts, the LULD checks or the settings saved in Setup, after a confirmation. Deleted history is compacted out of the
  database file, not just marked free.
- The reaction history is kept `reactions_keep_days` (365; 0 = forever) and pruned daily.
- *Forget* clears what this browser stored and signs it out, e.g. on a borrowed device.
- Every file under `data/` is readable by you only.

**Backup** (Setup → Your data; `app/backup.py`): *Download backup* saves your setup as a
file, to restore here or on another computer: the watchlist, news keywords, alert rules, the
alert switches, phone categories, muted alert kinds, and the Setup values you've changed from
the defaults (signal thresholds, recap time…).

- Never in it: any secret (access token, ntfy topic and token, Telegram ids, Finnhub key, your
  SEC contact email), this computer's address (`public_url`) or the day's snoozes.
- *Restore from a file…* reads it and lists every change (*now* and *then*) before anything
  changes; *Restore these* applies them. Every value is checked as the dashboard would check
  it: tickers, rule types and values, setting ranges. A secret in the file, an unknown entry
  or a setting this version doesn't have is left out and said so. Alert rules get new ids.
- The file is plain JSON. It names the stocks you watch and your rule levels, so keep it as
  private as you'd keep the dashboard.

## Real-time prices (optional)

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

## Phone alerts (optional)

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
  and watchlist headlines), SEC filings, unusual volume, breakouts, the pre-market brief, the daily recap and
  data outages.
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
  minute. Halts arrive as urgent notifications, price, warrant, offering-price and breakout alerts as
  high priority, news and float rotation as normal.
- Set `public_url` (e.g. `http://192.168.1.20:8000`) to make a tapped alert open that
  ticker's detail panel. That only works where your phone can reach the app, e.g. on the same Wi-Fi,
  so set an access token first (next section).
- Delivery failures appear under *Phone* in red. Tokens and topics are kept out of error
  messages and logs.

## Keep it running in the background

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

## Record and replay

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

## Opening it from your phone or another computer

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

## HTTPS with Market Pulse's own certificate

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

