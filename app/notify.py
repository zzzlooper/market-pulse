"""Push alerts to your phone via ntfy (https://ntfy.sh, or self-hosted) and/or a Telegram bot.

Nothing is sent unless credentials are configured. Delivery runs in a background task so a
slow network never holds up the dashboard. A burst of alerts is collapsed into a few
messages plus a summary, and a per-minute cap stops runaway notification storms.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx

from .config import Settings

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
BATCH_WINDOW = 2.0      # seconds to gather a burst before sending
PER_BATCH = 3           # individual messages per burst; the rest become one summary
PER_MINUTE = 20         # hard cap across bursts
QUEUE_LIMIT = 500

# alert kind -> (category, ntfy priority, ntfy tag/emoji)
ALERT_KINDS = {
    "above": ("price", "high", "chart_with_upwards_trend"),
    "below": ("price", "high", "chart_with_downwards_trend"),
    "move": ("price", "high", "zap"),
    "rvol": ("price", "default", "bar_chart"),
    "unusual": ("unusual", "default", "bar_chart"),
    "halt": ("halts", "urgent", "rotating_light"),
    "resume": ("halts", "high", "arrow_forward"),
    "warrant": ("price", "high", "warning"),
    "offering_below": ("price", "high", "chart_with_downwards_trend"),
    "offering_above": ("price", "high", "chart_with_upwards_trend"),
    "rotation": ("unusual", "default", "arrows_counterclockwise"),
    "pmh": ("breakouts", "high", "rocket"),
    "hod": ("breakouts", "high", "chart_with_upwards_trend"),
    "resuming": ("halts", "high", "hourglass_flowing_sand"),
    "resumed": ("halts", "high", "arrow_forward"),
    "luld_up": ("halts", "high", "warning"),
    "luld_down": ("halts", "high", "warning"),
}


def from_alert(event: dict, public_url: str = "") -> dict:
    category, priority, tag = ALERT_KINDS.get(event["kind"], ("price", "default", "bell"))
    titles = {"halt": "trading halted", "resume": "trading resumes", "unusual": "unusual volume",
              "warrant": "warrants in the money", "offering_below": "below offering price",
              "offering_above": "back above offering price", "rotation": "float rotated",
              "pmh": "broke pre-market high", "hod": "new high of day", "resuming": "resumes in a minute",
              "resumed": "resumed", "luld_up": "near limit-up band", "luld_down": "near limit-down band"}
    return {
        "category": category,
        "title": f"{event['symbol']} · {titles.get(event['kind'], 'price alert')}",
        "body": event["message"],
        "url": f"{public_url.rstrip('/')}/#ticker={event['symbol']}" if public_url else None,
        "priority": priority,
        "tag": tag,
    }


def from_news(item: dict) -> dict:
    tickers = " ".join(item["tickers"][:4])
    mood = item.get("sentiment", {}).get("label")
    return {
        "category": "news",
        "title": f"{tickers + ' · ' if tickers else ''}{item['source']}",
        "body": item["title"],
        "url": item["url"] or None,
        "priority": "default",
        "tag": {"bullish": "green_circle", "bearish": "red_circle"}.get(mood, "newspaper"),
    }


# filing kind -> (ntfy priority, ntfy tag/emoji)
FILING_KINDS = {
    "dilution": ("high", "warning"),
    "insider_buy": ("default", "moneybag"),
    "insider_sell": ("default", "outbox_tray"),
    "stake": ("default", "dart"),
}


def from_filing(item: dict, public_url: str = "") -> dict:
    """A news item from FilingsFeed. Says why it matters when the stock isn't on the watchlist."""
    filing, symbol = item["filing"], item["tickers"][0]
    priority, tag = FILING_KINDS.get(filing["kind"], ("default", "page_facing_up"))
    notes = [filing["hot"]] if filing["hot"] not in (None, "watchlist") else []
    if filing.get("risk") in ("high", "medium"):
        notes.append(f"dilution risk {filing['risk'].upper()}")
    why = f" ({', '.join(notes)})" if notes else ""
    return {
        "category": "filings",
        "title": f"{symbol} · {filing['label']}",
        "body": f"{item['title']}{why}",
        "url": f"{public_url.rstrip('/')}/#ticker={symbol}" if public_url else item["url"] or None,
        "priority": priority,
        "tag": tag,
    }


def from_outage(event: dict) -> dict:
    """A source-health event from SourceHealth.outages()."""
    if event["kind"] == "down":
        return {"category": "sources", "title": f"Market Pulse · {event['label']} down",
                "body": f"No data for {event['minutes']} min: {event['reason']}. "
                        "Alerts that depend on it can't fire until it's back.",
                "url": None, "priority": "default", "tag": "warning"}
    return {"category": "sources", "title": f"Market Pulse · {event['label']} back",
            "body": f"Receiving data again after {event['minutes']} min.",
            "url": None, "priority": "low", "tag": "white_check_mark"}


def summary(notes: list[dict]) -> dict:
    symbols = list(dict.fromkeys(n["title"].split(" · ")[0] for n in notes))
    shown = ", ".join(symbols[:8]) + (" …" if len(symbols) > 8 else "")
    return {"category": "summary", "title": f"Market Pulse · +{len(notes)} more alerts",
            "body": shown, "url": None, "priority": "default", "tag": "bell"}


def mask(secret: str, keep: int = 4) -> str:
    return f"…{secret[-keep:]}" if len(secret) > keep else "…"


class PhoneNotifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None,
                 on_status: Callable[[dict], Awaitable[None]] | None = None):
        self.settings = settings
        self._client = client or httpx.AsyncClient(timeout=10)
        self._on_status = on_status  # told after every delivery attempt, so the UI can show errors
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self._sent: deque[float] = deque()  # send times within the last minute
        self.last_sent: str | None = None
        self.last_error: str | None = None

    @property
    def channels(self) -> list[str]:
        s = self.settings
        return [name for name, on in (("ntfy", s.ntfy_topic), ("telegram", s.telegram_bot_token and s.telegram_chat_id)) if on]

    def status(self) -> dict:
        s = self.settings
        targets = []
        if s.ntfy_topic:
            targets.append(f"ntfy {s.ntfy_server.removeprefix('https://')}/{mask(s.ntfy_topic)}")
        if s.telegram_bot_token and s.telegram_chat_id:
            targets.append(f"Telegram chat {mask(str(s.telegram_chat_id))}")
        return {"targets": targets, "last_sent": self.last_sent, "last_error": self.last_error}

    def enqueue(self, notes: list[dict], categories: dict[str, bool]) -> None:
        if not self.channels:
            return
        for note in notes:
            if not categories.get(note["category"], False):
                continue
            try:
                self._queue.put_nowait(note)
            except asyncio.QueueFull:
                log.warning("phone notification queue full; dropping %s", note["title"])

    async def run(self) -> None:
        """Background sender: gathers bursts, caps volume, sends to every configured channel."""
        while True:
            batch = [await self._queue.get()]
            deadline = time.monotonic() + BATCH_WINDOW
            while (left := deadline - time.monotonic()) > 0:
                try:
                    batch.append(await asyncio.wait_for(self._queue.get(), left))
                except TimeoutError:
                    break
            outgoing = batch if len(batch) <= PER_BATCH else [*batch[:PER_BATCH - 1], summary(batch[PER_BATCH - 1:])]
            for note in outgoing:
                if not self._allow():
                    log.warning("phone notifications capped at %d/min; dropped %s", PER_MINUTE, note["title"])
                    continue
                await self.deliver(note)

    async def send_test(self) -> dict:
        if not self.channels:
            raise RuntimeError("No phone channel configured; see README (ntfy or Telegram)")
        await self.deliver({"category": "test", "title": "Market Pulse test",
                            "body": "Phone alerts are working.", "url": None, "priority": "default",
                            "tag": "white_check_mark"})
        return self.status()

    async def deliver(self, note: dict) -> None:
        results = await asyncio.gather(*(getattr(self, f"_send_{ch}")(note) for ch in self.channels),
                                       return_exceptions=True)
        failures = [f"{ch}: {r}" for ch, r in zip(self.channels, results) if isinstance(r, BaseException)]
        self.last_error = "; ".join(failures) or None
        if failures:
            log.warning("phone notification failed: %s", self.last_error)
        if len(failures) < len(results):  # at least one channel got it
            self.last_sent = datetime.now(UTC).isoformat()
        if self._on_status:
            await self._on_status(self.status())

    async def aclose(self) -> None:
        await self._client.aclose()

    def _allow(self) -> bool:
        now = time.monotonic()
        while self._sent and now - self._sent[0] > 60:
            self._sent.popleft()
        if len(self._sent) >= PER_MINUTE:
            return False
        self._sent.append(now)
        return True

    async def _send_ntfy(self, note: dict) -> None:
        s = self.settings
        # Header values must be latin-1; titles are ASCII apart from the "·" separator.
        headers = {
            "Title": note["title"].replace("·", "-").encode("latin-1", "replace").decode("latin-1"),
            "Priority": note["priority"],
            "Tags": note["tag"],
        }
        if note["url"]:
            headers["Click"] = note["url"]
        if s.ntfy_token:
            headers["Authorization"] = f"Bearer {s.ntfy_token}"
        resp = await self._client.post(f"{s.ntfy_server.rstrip('/')}/{s.ntfy_topic}",
                                       content=note["body"].encode(), headers=headers)
        if resp.status_code != 200:
            # Not raise_for_status(): its message includes the URL, and on public ntfy.sh the
            # topic name is effectively the password.
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200] or resp.reason_phrase}")

    async def _send_telegram(self, note: dict) -> None:
        s = self.settings
        text = f"{note['title']}\n{note['body']}" + (f"\n{note['url']}" if note["url"] else "")
        resp = await self._client.post(
            f"{s.telegram_api.rstrip('/')}/bot{s.telegram_bot_token}/sendMessage",
            json={"chat_id": s.telegram_chat_id, "text": text, "disable_web_page_preview": True,
                  "disable_notification": note["priority"] not in ("high", "urgent")},
        )
        if resp.status_code != 200:
            # Telegram explains failures in the body ("chat not found", "bot was blocked"...);
            # say that rather than echo the URL, which contains the bot token.
            detail = resp.json().get("description", resp.reason_phrase) if resp.content else resp.reason_phrase
            raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
