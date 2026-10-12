"""Settings (from config.toml) and the user's runtime state (watchlist, keywords)."""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "data" / "state.json"

DEFAULT_FEEDS = {
    "SEC 8-K": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=40&output=atom",
    "GlobeNewswire": "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies",
    "PR Newswire": "https://www.prnewswire.com/rss/news-releases-list.rss",
}
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "TSLA", "AMD", "SPY"]
DEFAULT_KEYWORDS = ["FDA", "acquisition", "merger", "offering", "guidance", "buyback"]


@dataclass
class Settings:
    # SEC asks automated clients to identify themselves: "app-name contact@email".
    sec_user_agent: str = "market-pulse/0.1 admin@example.com"
    # Real-time watchlist prices; empty = delayed Yahoo prices only.
    finnhub_api_key: str = ""
    finnhub_quotes_interval: float = 300.0  # REST snapshot refresh; trades stream in between
    # Phone alerts (all optional). ntfy: pick a hard-to-guess topic, since anyone who knows it
    # can read it on the public server. Telegram: a bot token from @BotFather + your chat id.
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""          # only for protected topics / self-hosted servers
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api: str = "https://api.telegram.org"
    public_url: str = ""          # e.g. http://192.168.1.20:8000, so a tapped alert opens the ticker
    # Required to use the app when set. Leave empty only while it listens on 127.0.0.1.
    access_token: str = ""
    source_alert_minutes: float = 5.0  # phone alert when a data source is down this long; 0 = never
    quotes_interval: float = 15.0
    movers_interval: float = 60.0
    news_interval: float = 30.0
    halts_interval: float = 20.0
    rvol_threshold: float = 2.0
    min_volume: int = 300_000
    news_history: int = 500
    feeds: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FEEDS))
    ticker_news: bool = True  # also poll Yahoo's per-ticker RSS for each watchlist symbol


# Settings that may come from same-named upper-case env vars (which win), so secrets can
# stay out of config.toml.
SECRET_ENV = ("finnhub_api_key", "ntfy_topic", "ntfy_token", "telegram_bot_token", "telegram_chat_id",
              "access_token")


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    unknown = set(raw) - {f.name for f in fields(Settings)}
    if unknown:
        raise ValueError(f"Unknown settings in {path.name}: {', '.join(sorted(unknown))}")
    for name in SECRET_ENV:
        if value := os.environ.get(name.upper()):
            raw[name] = value
    if "telegram_chat_id" in raw:
        raw["telegram_chat_id"] = str(raw["telegram_chat_id"])  # TOML may give an int
    return Settings(**raw)


# Which alert categories go to the phone. Unusual volume is off: it can fire dozens of times a day.
# "sources" is a data source going down (and coming back), so silence isn't mistaken for a calm market.
DEFAULT_PHONE_ALERTS = {"price": True, "halts": True, "news": True, "unusual": False, "sources": True}

DEFAULT_ALERT_RULES = [
    {"id": "default-move", "symbol": "", "kind": "move", "value": 5.0},
    {"id": "default-rvol", "symbol": "", "kind": "rvol", "value": 3.0},
]


class UserState:
    """Watchlist, keywords and alert rules, editable from the UI and persisted to JSON."""

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.watchlist: list[str] = data.get("watchlist", list(DEFAULT_WATCHLIST))
        self.keywords: list[str] = data.get("keywords", list(DEFAULT_KEYWORDS))
        self.alert_rules: list[dict] = data.get("alert_rules", [dict(r) for r in DEFAULT_ALERT_RULES])
        self.unusual_alerts: bool = data.get("unusual_alerts", True)
        self.halt_alerts: str = data.get("halt_alerts", "watchlist")  # "watchlist" | "all" | "off"
        self.phone_alerts: dict[str, bool] = {**DEFAULT_PHONE_ALERTS, **data.get("phone_alerts", {})}

    def to_dict(self) -> dict:
        return {
            "watchlist": self.watchlist,
            "keywords": self.keywords,
            "alert_rules": self.alert_rules,
            "unusual_alerts": self.unusual_alerts,
            "halt_alerts": self.halt_alerts,
            "phone_alerts": self.phone_alerts,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(self.path)
