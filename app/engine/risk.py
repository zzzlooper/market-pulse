"""Dilution-risk summaries (the DIL badges) for the watchlist and the movers lists, and the
risk level a filing alert carries."""
from __future__ import annotations

import asyncio
import logging

from .. import market
from ..clock import clock
from ..dilution import summary as risk_summary
from ..health import SourceError, describe
from ..warrants import in_the_money_message

log = logging.getLogger(__name__)

RISK_INTERVAL = 60.0      # how often the risk loop looks for symbols without a fresh profile
RISK_RETRY = 900.0        # after a failed profile, wait this long before trying the symbol again
RISK_ALERT_WAIT = 8.0     # a filing alert waits at most this long for the stock's risk level



class RiskMixin:
    """Owns risk (symbol -> summary), the failure back-off and the _wake_risk signal."""

    def _init_risk(self) -> None:
        self.risk: dict[str, dict | None] = {}  # symbol -> dilution summary; None = not an SEC filer
        self._risk_failed: dict[str, float] = {}  # symbol -> loop time of the last failure
        self._wake_risk = asyncio.Event()
        self._warrant_last: dict[str, float] = {}             # symbol -> last price seen
        self._warrant_fired: dict[tuple, object] = {}         # (symbol, kind, price) -> day it fired

    def _risk_symbols(self) -> list[str]:
        """Watchlist first, then the movers lists where dilution matters most."""
        symbols = list(self.state.watchlist)
        for key in self.settings.risk_movers:
            symbols += [row["symbol"] for row in self.movers.get(key, [])]
        stocks = [s for s in dict.fromkeys(symbols) if not s.startswith("^") and "=" not in s]  # no indices/futures
        return stocks[:self.settings.risk_max_symbols]

    async def _risk_profile(self, symbol: str) -> dict | None:
        """Fetch (or reuse) a profile and publish its summary if it changed. Raises on failure."""
        try:
            profile = await self.dilution.get(symbol)
        except SourceError:
            raise  # SEC is unavailable, not this symbol's fault
        except Exception:
            self._risk_failed[symbol] = asyncio.get_running_loop().time()
            raise
        self._risk_failed.pop(symbol, None)
        summary = risk_summary(profile)
        if symbol not in self.risk or self.risk[symbol] != summary:
            self.risk[symbol] = summary
            await self.hub.broadcast({"type": "risk", "data": {symbol: summary}})
        return summary

    async def _check_warrants(self, rows: list[dict]) -> None:
        """Alert when a stock crosses up through a warrant exercise price: from then on those
        warrants can be exercised at a profit, and the new shares sold. The first price seen is
        only a baseline; each level fires at most once a day."""
        today = clock.today(market.ET)
        fired = []
        for row in rows:
            symbol, price = row.get("symbol"), row.get("price")
            levels = (self.risk.get(symbol) or {}).get("warrant_levels") if symbol else None
            if price is None or not levels:
                continue
            before, self._warrant_last[symbol] = self._warrant_last.get(symbol), price
            if before is None:
                continue
            for level in levels:
                key = (symbol, level["kind"], level["price"])
                if before < level["price"] <= price and self._warrant_fired.get(key) != today:
                    self._warrant_fired[key] = today
                    fired.append(self.alerts.event(row, "warrant", in_the_money_message(symbol, price, level)))
        if fired:
            await self._publish_alerts(self.alerts.record(fired))

    async def _risk_level(self, symbol: str) -> str | None:
        try:
            summary = await asyncio.wait_for(self._risk_profile(symbol), RISK_ALERT_WAIT)
        except Exception as exc:  # the alert goes out without it
            log.info("no dilution risk for %s alert: %s", symbol, describe(exc))
            return None
        return summary["level"] if summary else None

    async def _update_risk(self) -> None:
        """Keep risk summaries fresh for the watched and moving stocks, one symbol at a time
        so SEC requests for news and filings aren't held up."""
        now = asyncio.get_running_loop().time()
        for symbol in self._risk_symbols():
            if self.dilution.is_fresh(symbol) and symbol in self.risk:
                continue
            if now - self._risk_failed.get(symbol, -RISK_RETRY) < RISK_RETRY:
                continue
            try:
                await self._risk_profile(symbol)
            except SourceError as exc:
                log.warning("dilution risk paused: %s", exc)
                return  # SEC itself is unavailable; the next round tries again
            except Exception as exc:
                log.info("dilution risk for %s failed: %s", symbol, describe(exc))
