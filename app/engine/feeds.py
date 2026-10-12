"""News headlines, SEC filings and trading halts: polling them, alerting on them, and handing
the catalysts among them to reaction tracking."""
from __future__ import annotations

import asyncio

from ..halts import halt_messages
from ..health import SourceError
from ..notify import from_filing, from_news

HALT_LABELS = {"news": "news halt", "volatility": "volatility pause", "regulatory": "regulatory halt"}


class FeedsMixin:
    """Uses news, filings and halts (the feeds) and the _wake_news signal."""

    def _init_feeds(self) -> None:
        self._wake_news = asyncio.Event()

    async def _update_news(self) -> str | None:
        fresh = await self.news.poll(list(self.state.watchlist), list(self.state.keywords))
        if fresh:
            await self.hub.broadcast({"type": "news", "data": fresh})
            self.phone.enqueue([from_news(n) for n in fresh if n["alert"]], self.state.phone_alerts)
            self._wake_gaps.set()  # a fresh headline may explain (or start) a gap
            for n in fresh:
                if n["alert"] and n["tickers"]:
                    self._track(n["id"], n["tickers"][0], "headline", f"headline ({n['sentiment']['label']})",
                                n["title"], n["published"])
        total, failing = self.news.source_count, self.news.failing
        if total and len(self.news.failed) == total:
            raise SourceError(f"all {total} feeds failed")
        return f"{len(failing)} of {total} feeds failing: {', '.join(failing)}" if failing else None

    def _latest_news(self, limit: int = 300) -> list[dict]:
        items = self.news.latest(limit) + (self.filings.latest() if self.filings else [])
        return sorted(items, key=lambda n: n["published"], reverse=True)[:limit]

    async def _update_filings(self) -> str | None:
        fresh = await self.filings.poll(list(self.state.watchlist), self._movers_by_symbol())
        alerting = list(dict.fromkeys(n["tickers"][0] for n in fresh if n["alert"]))
        if alerting:
            # An alert is more useful with the risk level, but shouldn't wait long for it.
            levels = await asyncio.gather(*(self._risk_level(sym) for sym in alerting))
            by_symbol = dict(zip(alerting, levels))
            for n in fresh:
                n["filing"]["risk"] = by_symbol.get(n["tickers"][0])
        for n in fresh:
            f, symbol = n["filing"], n["tickers"][0]
            if f["kind"] == "report" and not f["hot"]:
                continue  # routine 8-Ks: hundreds a day, tracked only for watched and moving stocks
            risk = f.get("risk") or (self.risk.get(symbol) or {}).get("level")
            if self._track(n["id"], symbol, f["kind"], f["label"], n["title"], n["published"], risk) \
                    and f["kind"] == "dilution" and risk is None:
                self._risk_queue.put_nowait((n["id"], symbol))  # so offerings can be compared by risk
        if fresh:
            await self.hub.broadcast({"type": "news", "data": fresh})
            self.phone.enqueue([from_filing(n, self.settings.public_url) for n in fresh if n["alert"]],
                               self.state.phone_alerts)
            self._wake_gaps.set()
        failing = self.filings.failing
        return f"EDGAR feeds failing: {', '.join(failing)}" if failing else None

    async def _update_halts(self) -> None:
        changed, new, resumption_set = await self.halts.poll()
        await self._apply_halts(changed, new, resumption_set)

    async def _apply_halts(self, changed: bool, new: list[dict], resumption_set: list[dict]) -> None:
        if changed:
            await self.hub.broadcast({"type": "halts", "data": self.halts.latest()})
        for halt in new:
            self._track(f"halt:{halt['symbol']}:{halt['halted_at']}", halt["symbol"], "halt",
                        HALT_LABELS.get(halt["category"], "halt"), f"{halt['symbol']} halted · {halt['reason']}",
                        halt["halted_at"])
        messages = halt_messages(new, resumption_set, self.state.watchlist, self.state.halt_alerts)
        await self._publish_alerts(self.alerts.record(
            [self.alerts.event(halt, kind, message) for halt, kind, message in messages]))
