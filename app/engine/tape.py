"""Today's tape for the watched and moving stocks: the short sale restriction (SSR badges, rules
in app/ssr.py), float rotation (ROT badges, and an alert when a stock trades its whole float) and
breakouts (alerts, rules in app/breakouts.py)."""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime

from .. import market
from ..breakouts import Breakouts, money
from ..clock import clock
from ..halts import relevant
from ..luld import Bands, Checks, tier
from ..ssr import carried_over, triggered
from ..warrants import shares_text

TAPE_INTERVAL = 60.0    # how often new symbols get their daily bars and float looked up
TAPE_MAX_SYMBOLS = 150  # watchlist, gappers and movers
FLOAT_BATCH = 20        # float lookups per round: one Yahoo request each
TAPE_TRIES = 3          # a symbol Yahoo has nothing on is given up on for the day after this
LULD_COOLDOWN = 900     # seconds between band warnings for one stock and direction
LULD_SEND_EVERY = 30    # the near-a-band stocks go to the browsers at most this often, unless one changes
LULD_HISTORY = 120      # estimates kept per stock, to compare a pause with the one from before it


def day2_move(days: list[dict], today: str, min_gain: float = 0.30) -> dict | None:
    """From daily bars ({"date", "low", "close"}, oldest first): the last session's gain if it was
    at least min_gain, so today is its day 2."""
    past = [d for d in days if d["date"] < today and d["close"]]
    if len(past) < 2:
        return None
    before, last = past[-2], past[-1]
    gain = last["close"] / before["close"] - 1
    return {"date": last["date"], "change_pct": round(gain * 100, 1)} if gain >= min_gain else None


class TapeMixin:
    """Owns ssr (symbol -> {"since", "today"}), floats (symbol -> shares), day2 (yesterday's big
    gainers) and the rotation and breakout alerts."""

    def _init_tape(self) -> None:
        self.ssr: dict[str, dict] = {}            # in effect today; "today": triggered today, so tomorrow too
        self.floats: dict[str, int] = {}          # symbol -> shares in the public float, when Yahoo knows it
        self.day2: dict[str, dict] = {}           # symbol -> {"date", "change_pct"}: a big gainer last session
        self._tape_day: str | None = None
        self._tape_tries: dict[tuple[str, str], int] = {}  # (what, symbol) -> lookups today
        self._tape_done: set[tuple[str, str]] = set()      # (what, symbol) looked up today
        self._rotation_last: dict[str, float] = {}         # symbol -> last volume / float seen
        self._rotation_fired: set[str] = set()             # today
        self.breakouts = Breakouts()
        self.luld: dict[str, dict] = {}                    # symbol -> estimated bands, today
        self.market_caps: dict[str, float] = {}            # symbol -> market cap, for the LULD tier
        self.industries: dict[str, str] = {}               # symbol -> Yahoo industry, for sympathy moves
        self.sympathy: dict[str, dict] = {}                # symbol -> its industry's leader today
        self._sympathy_alerted: set[str] = set()
        self._sympathy_seen: set[str] = set()              # checked with its industry known: later checks can alert
        self._sympathy_sent: frozenset = frozenset()
        self._luld_bands = Bands()
        self._luld_warned: dict[tuple[str, str], float] = {}
        self._luld_near_at: dict[tuple[str, str], float] = {}  # (symbol, side) -> last time seen near it
        self._luld_history: dict[str, deque] = {}  # symbol -> (time, estimate), the last few minutes
        self.luld_checks = Checks(self.state.path.parent / "luld_checks.json")  # mode 600
        self._luld_sent: tuple[float, frozenset] = (0.0, frozenset())
        self._wake_tape = asyncio.Event()

    def _tape_today(self) -> str:
        """Today's date (ET); a new day starts over: yesterday's SSR is looked up again from the bars."""
        today = clock.today(market.ET).isoformat()
        if today != self._tape_day:
            self._tape_day = today
            self.ssr, self.day2, self._tape_tries, self._tape_done = {}, {}, {}, set()
            self.luld, self._luld_warned, self._luld_history = {}, {}, {}
            self.sympathy, self._sympathy_alerted, self._sympathy_seen = {}, set(), set()
            self._luld_bands.forget()
            self._rotation_last, self._rotation_fired = {}, set()
        return today

    def _tape_symbols(self) -> list[str]:
        symbols = [*self.state.watchlist, *(g["symbol"] for g in self.gaps)]
        for rows in self.movers.values():
            symbols += [row["symbol"] for row in rows]
        stocks = [s for s in dict.fromkeys(symbols) if not s.startswith("^") and "=" not in s]  # no indices/futures
        return stocks[:TAPE_MAX_SYMBOLS]

    def _tape_todo(self, what: str, symbols: list[str]) -> list[str]:
        return [s for s in symbols if (what, s) not in self._tape_done]

    def _tape_got(self, what: str, asked: list[str], got: set[str]) -> None:
        for s in asked:
            self._tape_tries[(what, s)] = tries = self._tape_tries.get((what, s), 0) + 1
            if s in got or tries >= TAPE_TRIES:
                self._tape_done.add((what, s))

    async def _update_tape(self) -> None:
        """Look up what's missing for today's symbols: the last sessions' bars (an SSR carried
        over from yesterday) and the float."""
        today = self._tape_today()
        symbols = self._tape_symbols()
        if todo := self._tape_todo("daily", symbols):
            rows = await self._call("fetch_daily", market.fetch_daily, todo)
            self._tape_got("daily", todo, {r["symbol"] for r in rows})
            carried = {r["symbol"]: since for r in rows if (since := carried_over(r["days"], today))}
            new = {s: {"since": since, "today": False} for s, since in carried.items() if s not in self.ssr}
            if new:
                self.ssr.update(new)
                await self.hub.broadcast({"type": "ssr", "data": dict(self.ssr)})
            min_gain = self.settings.day2_min_pct / 100
            if runners := {r["symbol"]: move for r in rows if (move := day2_move(r["days"], today, min_gain))}:
                self.day2.update(runners)
                await self.hub.broadcast({"type": "day2", "data": dict(self.day2)})
        if todo := self._tape_todo("float", symbols)[:FLOAT_BATCH]:
            rows = await self._call("fetch_floats", market.fetch_floats, todo)
            self._tape_got("float", todo, {r["symbol"] for r in rows})
            self.market_caps.update({r["symbol"]: r["market_cap"] for r in rows if r.get("market_cap")})
            self.industries.update({r["symbol"]: r["industry"] for r in rows if r.get("industry")})
            new = {r["symbol"]: int(r["float_shares"]) for r in rows
                   if r.get("float_shares") and self.floats.get(r["symbol"]) != int(r["float_shares"])}
            if new:
                self.floats.update(new)
                await self.hub.broadcast({"type": "floats", "data": dict(self.floats)})

    async def _check_tape(self, rows: list[dict]) -> None:
        """New prices: has a stock tripped SSR today, or traded its whole float?"""
        today = self._tape_today()
        regular = market.is_regular_bar(int(clock.time()))
        ssr_changed, fired = False, []
        for row in rows:
            symbol = row.get("symbol")
            if not symbol or row.get("session") not in (None, today):
                continue  # before the open, a quote still describes the last session
            if row.get("session") == today and not self.ssr.get(symbol, {}).get("today") and (
                    triggered(row.get("low"), row.get("prev_close"))
                    or (regular and triggered(row.get("price"), row.get("prev_close")))):
                self.ssr[symbol] = {"since": today, "today": True}
                ssr_changed = True
            float_shares, volume = self.floats.get(symbol), row.get("volume")
            if not float_shares or not volume:
                continue
            rotation = volume / float_shares
            before, self._rotation_last[symbol] = self._rotation_last.get(symbol), rotation
            multiple = self.settings.rotation_multiple
            if (before is not None and before < multiple <= rotation and symbol not in self._rotation_fired
                    and self.state.rotation_alerts):
                self._rotation_fired.add(symbol)
                how_much = "its whole float" if multiple == 1 else f"{multiple:g}× its float"
                fired.append(self.alerts.event(row, "rotation", (
                    f"{symbol} has traded {how_much}: {shares_text(volume)} shares today, "
                    f"{rotation:.1f}× the {shares_text(float_shares)}-share float")))
        if ssr_changed:
            await self.hub.broadcast({"type": "ssr", "data": dict(self.ssr)})
        if fired:
            await self._publish_alerts(fired)
        todays = [r for r in rows if r.get("session") in (None, today)]
        await self._check_sympathy()
        await self._check_breakouts(todays, regular=regular)
        if regular:
            await self._check_luld(todays)

    async def _check_sympathy(self) -> None:
        """Sympathy moves among the stocks followed today (watchlist, movers): in each industry the
        biggest gainer up sympathy_lead_pct or more leads, and its peers up sympathy_min_pct or more
        move with it. A stock's first check (once its industry is known) is a baseline, and so is its
        leader's, so one already moving at startup isn't alerted, even if its leader's industry
        arrives later. One alert per stock a day, with the breakouts
        (same switch, same phone category).
        Once in, a stock stays until it falls well back (a leader below 80% of the threshold, a
        follower below half), so one hovering at a threshold doesn't flicker in and out."""
        today, s = self._tape_today(), self.settings
        moves: dict[str, dict] = {}
        for row in [*self.quotes, *(r for rows in self.movers.values() for r in rows)]:
            if row.get("session") in (None, today) and row.get("change_pct") is not None:
                moves.setdefault(row["symbol"], row)
        led = {v["leader"] for v in self.sympathy.values()}
        leaders: dict[str, tuple[str, float]] = {}
        for symbol, row in moves.items():
            industry = self.industries.get(symbol)
            bar = s.sympathy_lead_pct * (0.8 if symbol in led else 1)
            if industry and row["change_pct"] >= bar and row["change_pct"] > leaders.get(industry, ("", 0))[1]:
                leaders[industry] = (symbol, row["change_pct"])
        sympathy, fired = {}, []
        for symbol, row in moves.items():
            industry = self.industries.get(symbol)
            lead = leaders.get(industry) if industry else None
            bar = s.sympathy_min_pct * (0.5 if symbol in self.sympathy else 1)
            if not lead or lead[0] == symbol or row["change_pct"] < bar:
                continue
            sympathy[symbol] = {"industry": industry, "leader": lead[0], "leader_pct": round(lead[1], 1),
                                "change_pct": round(row["change_pct"], 1)}
            if not {symbol, lead[0]} <= self._sympathy_seen:  # already moving at its or its leader's first check
                self._sympathy_alerted.add(symbol)
            if symbol not in self._sympathy_alerted and self.state.breakout_alerts:
                self._sympathy_alerted.add(symbol)
                fired.append(self.alerts.event(row, "sympathy", (
                    f"{symbol} {row['change_pct']:+.1f}% in sympathy with {lead[0]} {lead[1]:+.0f}% ({industry})")))
        self.sympathy = sympathy
        self._sympathy_seen |= {symbol for symbol in moves if self.industries.get(symbol)}
        # Sent when who moves with whom changes; each one's move is in the quotes and movers anyway.
        if (pairs := frozenset((k, v["leader"]) for k, v in sympathy.items())) != self._sympathy_sent:
            self._sympathy_sent = pairs
            await self.hub.broadcast({"type": "sympathy", "data": dict(sympathy)})
        if fired:
            await self._publish_alerts(fired)

    def _check_luld_estimate(self, halt: dict) -> dict | None:
        """A LULD pause just appeared with its official pause price: how far off was the band
        estimated for that stock, and had the app seen it near that band within 15 minutes?"""
        symbol = halt["symbol"].replace(".", "-")  # Nasdaq BRK.B is Yahoo BRK-B
        halted = datetime.fromisoformat(halt["halted_at"]).timestamp()
        # The estimate from before the pause: during it, quotes repeat the last price and would
        # pull the reference toward it.
        before = [state for t, state in self._luld_history.get(symbol, ()) if t < halted]
        if not before or not halt.get("pause_price"):
            return None  # not a stock the app follows, or no price in the feed
        state, official = before[-1], halt["pause_price"]
        side = "up" if official > state["ref"] else "down"
        estimate = state["upper"] if side == "up" else state["lower"]
        near_at = self._luld_near_at.get((symbol, side))
        warned = near_at is not None and halted - LULD_COOLDOWN <= near_at <= halted
        return self.luld_checks.add(halt["halted_at"][:10], side, estimate, official, warned)

    def luld_near(self) -> dict[str, dict]:
        return {s: st for s, st in self.luld.items() if st["near"]}

    async def _check_luld(self, rows: list[dict]) -> None:
        """Estimate each stock's LULD bands (app/luld.py) and warn when the price nears one, for
        the stocks the halt-alert setting covers. At most every 15 minutes per stock and side."""
        now, fired = clock.time(), []
        for row in rows:
            symbol, price = row.get("symbol"), row.get("price")
            if not symbol or not price or symbol.startswith("^") or "=" in symbol:
                continue
            tier_ = tier(row.get("market_cap") or self.market_caps.get(symbol))
            self.luld[symbol] = state = self._luld_bands.update(symbol, price, now, tier_,
                                                                self.settings.luld_near_pct / 100)
            self._luld_history.setdefault(symbol, deque(maxlen=LULD_HISTORY)).append((now, state))
            side = state["near"]
            if side:
                self._luld_near_at[(symbol, side)] = now
            if not side or now - self._luld_warned.get((symbol, side), float("-inf")) < LULD_COOLDOWN:
                continue
            if not relevant({"symbol": symbol}, self.state.watchlist, self.state.halt_alerts):
                continue
            self._luld_warned[(symbol, side)] = now
            edge = state["upper"] if side == "up" else state["lower"]
            name = "limit-up" if side == "up" else "limit-down"
            pct = round((state["upper"] - state["ref"]) / state["ref"] * 100)
            fired.append(self.alerts.event(row, f"luld_{side}", (
                f"{symbol} is {state['away_pct']:.1f}% from its estimated {name} band of {money(edge)} "
                f"(reference {money(state['ref'])}, ±{pct}%): it pauses for 5 minutes there "
                "if it can't get back inside within 15 seconds")))
        # The browsers get the stocks near a band (the badges); the ticker panel asks for its own bands.
        near = frozenset((s, st["near"]) for s, st in self.luld.items() if st["near"])
        sent_at, sent_near = self._luld_sent
        if near != sent_near or (near and now - sent_at >= LULD_SEND_EVERY):
            self._luld_sent = (now, near)
            await self.hub.broadcast({"type": "luld", "data": self.luld_near()})
        if fired:
            await self._publish_alerts(fired)

    async def _check_breakouts(self, rows: list[dict], regular: bool) -> None:
        """Feed today's prices to the breakout rules; alert on what they find, unless switched off."""
        today, now, fired = self._tape_today(), clock.time(), []
        for row in rows:
            symbol, price = row.get("symbol"), row.get("price")
            if not symbol or price is None or symbol.startswith("^") or "=" in symbol:
                continue
            found = self.breakouts.update(symbol, price, day=today, now=now, regular=regular,
                                          high=row.get("high") if regular else None,
                                          pre_high=self.pre_highs.get(symbol),
                                          min_pullback=self.settings.breakout_pullback_pct / 100)
            if self.state.breakout_alerts:
                if (theme := self.sympathy.get(symbol)) and found:  # part of a theme: say whose
                    found = [(k, f"{m} · in sympathy with {theme['leader']} {theme['leader_pct']:+.0f}%") for k, m in found]
                fired += [self.alerts.event(row, kind, message) for kind, message in found]
        if fired:
            await self._publish_alerts(fired)
