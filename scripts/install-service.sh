#!/bin/sh
# Install Market Pulse as a systemd user service, so it keeps running (and sending phone
# alerts) after you close the terminal.
#
#   sh scripts/install-service.sh            install or update the service (doesn't start it)
#   sh scripts/install-service.sh --enable   ...and start it now and at every login
#   sh scripts/install-service.sh --uninstall
#
# Listening address: MP_HOST (default 127.0.0.1, this computer only) and MP_PORT (default 8000).
# MP_HOST=0.0.0.0 for phone access is refused unless an access token is set.
#
# Privacy: the unit file holds no secrets. Your SEC contact address and any tokens go in an
# environment file only you can read (~/.config/market-pulse/env, mode 600), or in config.toml,
# which git ignores. This script never prints their values, and access logs are off, so the
# journal doesn't record which devices opened the dashboard.
set -eu

APP_DIR=$(cd "$(dirname "$0")/.." && pwd)
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
UNIT_DIR=$CONFIG_HOME/systemd/user
UNIT=$UNIT_DIR/market-pulse.service
ENV_DIR=$CONFIG_HOME/market-pulse
ENV_FILE=$ENV_DIR/env
HOST=${MP_HOST:-127.0.0.1}
PORT=${MP_PORT:-8000}
SYSTEMCTL=${SYSTEMCTL:-systemctl}  # tests point this at a stub

case "${1:-}" in
  --uninstall)
    "$SYSTEMCTL" --user disable --now market-pulse.service 2>/dev/null || true
    rm -f "$UNIT"
    "$SYSTEMCTL" --user daemon-reload
    echo "Removed $UNIT. Your settings in $ENV_FILE and config.toml were left alone."
    exit 0 ;;
  --enable|"") ;;
  *) echo "usage: $0 [--enable|--uninstall]" >&2; exit 2 ;;
esac

case $APP_DIR in
  *[!A-Za-z0-9._/-]*) echo "The app's path has characters a unit file can't take safely: $APP_DIR" >&2; exit 1 ;;
esac
case $HOST in
  *[!A-Za-z0-9.:-]*) echo "MP_HOST doesn't look like an address: $HOST" >&2; exit 1 ;;
esac
case $PORT in
  ''|*[!0-9]*) echo "MP_PORT must be a number" >&2; exit 1 ;;
esac
if [ ! -x "$APP_DIR/.venv/bin/uvicorn" ]; then
  echo "No virtualenv with uvicorn in $APP_DIR/.venv: set it up first (see README, Run)." >&2
  exit 1
fi

# A private settings file for values that shouldn't sit in the project folder.
if [ ! -e "$ENV_FILE" ]; then
  (umask 077 && mkdir -p "$ENV_DIR" && cat > "$ENV_FILE" <<'EOF'
# Market Pulse settings for the background service. Only you can read this file (mode 600).
# Anything here overrides config.toml. Quote values that contain spaces.
#
# SEC asks for an app name and a contact email:
# SEC_USER_AGENT="market-pulse/0.4 you@example.com"
# ACCESS_TOKEN=          required before MP_HOST=0.0.0.0 (phone access)
# FINNHUB_API_KEY=
# NTFY_TOPIC=
# NTFY_TOKEN=
# TELEGRAM_BOT_TOKEN=
# TELEGRAM_CHAT_ID=
EOF
  )
  echo "Created $ENV_FILE (mode 600) for your contact address and tokens."
fi
chmod 600 "$ENV_FILE"

# The settings the service will actually run with: config.toml plus the env file.
# Only yes/no answers come back; no value is printed.
check() {
  (set -a && . "$ENV_FILE" && set +a && cd "$APP_DIR" && .venv/bin/python -c "
import sys
from app.config import load_settings
s = load_settings()
sys.exit(0 if $1 else 1)") 2>/dev/null
}

case $HOST in
  127.*|localhost|::1) ;;
  *)
    if ! check "s.access_token"; then
      echo "Refusing MP_HOST=$HOST without an access token: anyone on your network could change" >&2
      echo "your watchlist and alerts. Set ACCESS_TOKEN in $ENV_FILE (see README)." >&2
      exit 1
    fi ;;
esac
if ! check "'@example.com' not in s.sec_user_agent"; then
  echo "Note: sec_user_agent is still the example. Set SEC_USER_AGENT in $ENV_FILE or"
  echo "sec_user_agent in config.toml, or SEC may block news, filings and dilution data."
fi

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<EOF
# Written by scripts/install-service.sh; rerun it to change. No secrets in here: see $ENV_FILE.
[Unit]
Description=Market Pulse stock dashboard and alerts

[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=-$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host $HOST --port $PORT --no-access-log
Restart=on-failure
RestartSec=10
# data/state.json and anything else the app writes is readable by you only.
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
EOF
"$SYSTEMCTL" --user daemon-reload
echo "Installed $UNIT (listening on $HOST:$PORT)."

if [ "${1:-}" = "--enable" ]; then
  "$SYSTEMCTL" --user enable --now market-pulse.service
  "$SYSTEMCTL" --user restart market-pulse.service  # pick up a changed unit or settings
  echo "Running. Open http://localhost:$PORT  ·  logs: journalctl --user -u market-pulse -f"
else
  echo "Start it with: systemctl --user enable --now market-pulse"
fi
echo "To keep it running while you're logged out: loginctl enable-linger"
