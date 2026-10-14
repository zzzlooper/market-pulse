# Running Market Pulse yourself

A step-by-step guide, from nothing installed to a dashboard that runs in the background and
reaches your phone. Each step links to the detailed page if you want more. Back to the
[README](../README.md).

## What you need

- A computer that stays on during market hours: Linux is the best fit (the background service
  uses systemd). macOS works for running it by hand; on Windows use WSL.
- Python 3.11 or newer (tested on 3.11 and 3.14) and git.
- No accounts and no API keys to start. Optional extras later: a free Finnhub key for
  real-time prices, and ntfy or Telegram for phone alerts.

## 1. Install

```sh
git clone <your copy of the repository> market-pulse
cd market-pulse
python3 -m venv .venv
source .venv/bin/activate            # fish: source .venv/bin/activate.fish
pip install -r requirements.txt
```

## 2. First run

```sh
uvicorn app.main:app
```

Open http://localhost:8000. The first time, **Setup** opens with a checklist. Work down it:

1. **Your contact email for SEC.** SEC asks every automated client for one and may block
   requests without it. Use a real address you read; placeholder domains like example.com are
   refused. It's sent only to sec.gov, never shown again in full, and never written to logs.
2. **Make the watchlist yours.** Close Setup, remove the example tickers (×) and add yours in
   the *Add ticker* box. Add the news keywords you care about the same way.
3. **Enable alerts** (header button) for desktop notifications and a sound.

That's a working dashboard. Stop it with Ctrl+C. Everything you set is kept in `data/`
(readable by you only) and survives restarts.

Settings can also go in a file: `cp config.example.toml config.toml` and edit it. Setup shows
where each value comes from. Details: [setup.md](setup.md#settings-and-privacy).

## 3. Phone alerts (optional, recommended)

The quickest is ntfy, which needs no account:

1. Install the ntfy app on your phone.
2. In Setup → *Phone alerts*, press **Generate** next to *ntfy topic* and save. Copy the topic it
   shows (it's shown once).
3. In the ntfy app, subscribe to that topic.
4. In the dashboard's *Phone* box (bottom right), press **Send test**.

Anyone who knows the topic can read your alerts on the public server, so treat it like a
password. Telegram works too. Details: [setup.md](setup.md#phone-alerts-optional).

## 4. Keep it running in the background

So it keeps watching (and alerting) after you close the terminal:

```sh
sh scripts/install-service.sh --enable
```

This installs a systemd *user* service that starts now and at every login. Useful commands:

```sh
systemctl --user status market-pulse          # is it running?
journalctl --user -u market-pulse -f          # its log (settings by name only, never values)
systemctl --user restart market-pulse         # after changing config.toml or updating
sh scripts/install-service.sh --uninstall     # remove the service
```

A user service stops when you log out. To keep it running while logged out, enable lingering
once: `loginctl enable-linger "$USER"`. Details: [setup.md](setup.md#keep-it-running-in-the-background).

## 5. Open it from your phone (optional)

Only after you've set an access token, because on the network anyone on your Wi-Fi could
otherwise change your watchlist or switch your alerts off. Without a token the app refuses
every request not addressed to `localhost`.

1. In Setup → *Opening it from your phone*, press **Generate** next to *Access token* and save.
   Copy it: your phone will ask for it once.
2. Restart it listening on the network, with HTTPS so the token and your dashboard are
   encrypted on the Wi-Fi:

   ```sh
   MP_HOST=0.0.0.0 MP_HTTPS=1 sh scripts/install-service.sh --enable
   ```

3. On the phone, open `https://<this computer's address>:8000/market-pulse-ca.crt` and install
   the certificate as described in Setup → *Secure phone access* (it shows the fingerprint to
   compare). Then open `https://<this computer's address>:8000`, enter the token, and use *Add
   to Home Screen* to have it like an app.
4. Optionally set *Address your phone uses* in Setup (e.g. `https://192.168.1.20:8000`) so a
   tapped phone alert opens that ticker.

Find the computer's address with `ip -4 addr` (Linux) or in your router's device list. Use it
only on your own password-protected Wi-Fi; for access from outside, use a VPN to your home
network rather than opening a port to the internet. Details:
[setup.md](setup.md#opening-it-from-your-phone-or-another-computer) and
[HTTPS](setup.md#https-with-market-pulses-own-certificate).

## 6. Real-time prices (optional)

Without a key, Yahoo's prices are about 15 minutes delayed. A free key from
https://finnhub.io/register gives real-time watchlist prices: paste it in Setup → *Real-time
prices* and restart. Details: [setup.md](setup.md#real-time-prices-optional).

## Updating

```sh
# Setup → Your data → Download backup first, to be safe
git pull
source .venv/bin/activate && pip install -r requirements.txt
systemctl --user restart market-pulse        # or stop and start uvicorn again
```

Your watchlist, rules, settings and history are in `data/`, which updates don't touch.

## Running the tests

```sh
pip install -r requirements-dev.txt
python -m pytest
```

About 430 tests, offline: a fixture blocks the network, and they use a scratch config and data
folder, never yours. The browser tests need Chromium or Chrome installed and are skipped
without it.

## Trying it on a weekend

Turn on *Record the day's data for replay* in Setup on a trading day and restart. Later:

```sh
python -m app.replay list
python -m app.replay play data/recordings/<a recording>.jsonl.gz --from 09:00 --speed 10
```

and open http://localhost:8001. A replay never uses the network, never sends phone alerts and
never touches your real watchlist or history. Details: [setup.md](setup.md#record-and-replay).

## Troubleshooting

| What you see | What to do |
|---|---|
| *Address already in use* | Something else uses port 8000: `uvicorn app.main:app --port 8010`, or `MP_PORT=8010` for the service. |
| The header says a data source is down | Click it for which one and why. Yahoo sometimes rate-limits or changes things; it usually recovers within minutes. SEC needs your real contact email (step 2). One news feed failing now and then is normal. |
| No phone alerts | *Phone* box → **Send test**; errors show there in red. Check the category switches in the *Phone* box, and the *Show / Phone* columns under *Alert kinds*. |
| The phone can't open it | Was it started with `MP_HOST=0.0.0.0`? Is an access token set? Same Wi-Fi? A firewall may need port 8000 open on the home network. |
| Certificate warnings on the phone | Install and fully trust the CA certificate (step 5). Open it by address, not computer name. |
| The game plan is empty | The gap scan runs 04:00–10:00 ET on weekdays; started later, the plan stays empty until the next morning. |
| Times look off | The news feed shows your computer's local time; the ticker panel and halts use ET (New York). |

## Where your data is, and removing it

Everything is in the project folder: `data/` (your state, settings, history, recordings,
certificates, counts; readable by you only) and `config.toml` if you made one. Setup → *Your
data* lists each file with its size and can delete them. To remove Market Pulse entirely:
`sh scripts/install-service.sh --uninstall`, then delete the folder (and
`~/.config/market-pulse/` if the service script created it).
