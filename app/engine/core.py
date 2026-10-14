"""The Engine itself: wiring, lifecycle, the polling-loop runner, data-source health, the
snapshot browsers start from, and the actions the dashboard calls. The data areas are mixins
(see app/engine/__init__.py)."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from fastapi import WebSocket

from .. import market
from ..alerts import AlertMonitor, new_rule
from ..backup import changes as backup_changes
from ..backup import export as backup_export
from ..backup import read as backup_read
from ..clock import clock
from ..config import TLS_DIR, Settings, UserState
from ..datastore import (
    DELETABLE,
    alert_counts_item,
    delete_recordings,
    log_item,
    reactions_item,
    recordings_item,
    settings_item,
    state_item,
    tls_item,
)
from ..dilution import DilutionProfiles
from ..filings import FilingsFeed
from ..finnhub import FinnhubFeed
from ..halts import HaltFeed
from ..health import SourceError, SourceHealth, describe
from ..news import NewsFeed
from ..noise import KINDS as ALERT_KINDS
from ..noise import AlertCounts, muted, snoozed
from ..notify import PhoneNotifier, from_alert, from_outage
from ..replay import LiveIO, Recorder
from ..sec import SecClient
from ..setup import EDITABLE, DashboardSettings, SettingsError, apply, validate
from ..tls import status as tls_status
from .catalysts import REACTIONS_INTERVAL, TRACKED_ALERTS, CatalystsMixin
from .daily import DailyMixin
from .earnings import EARNINGS_INTERVAL, EarningsMixin
from .feeds import FeedsMixin
from .gaps import GAP_INTERVAL, GapsMixin
from .prices import PricesMixin
from .resumptions import RESUME_TICK, ResumptionsMixin
from .risk import RISK_INTERVAL, RiskMixin
from .tape import TAPE_INTERVAL, TapeMixin

log = logging.getLogger(__name__)

HEALTH_CHECK_SECONDS = 5.0


class Hub:
    """Connected browsers; every update is pushed to all of them."""

    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def send(self, ws: WebSocket, msg: dict) -> None:
        await ws.send_text(json.dumps(msg, default=str))

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await self.send(ws, msg)
            except Exception:
                self.clients.discard(ws)


class Engine(PricesMixin, FeedsMixin, RiskMixin, CatalystsMixin, DailyMixin, GapsMixin, EarningsMixin, TapeMixin,
             ResumptionsMixin):
    def __init__(self, settings: Settings, state: UserState, hub: Hub, health: SourceHealth | None = None,
                 io: LiveIO | None = None):
        self.settings = settings
        self.state = state
        self.hub = hub
        self.io = io or LiveIO()  # live data, or recording it, or replaying a recording (app/replay.py)

        # Sources. The SEC client is the only one that carries the user's contact address.
        self.sec = SecClient(settings.sec_user_agent, self.io.transport("sec"))
        self.news = NewsFeed(settings, self.sec, self.io.transport("news"))
        self.filings = FilingsFeed(settings, self.sec) if settings.sec_filings else None
        self.halts = HaltFeed(transport=self.io.transport("halts"))
        self.dilution = DilutionProfiles(self.sec)

        self._init_prices()
        self._init_feeds()
        self._init_risk()
        self._init_catalysts()
        self._init_gaps()
        self._init_earnings()
        self._init_tape()
        self._init_resumptions()
        self.finnhub = FinnhubFeed(settings.finnhub_api_key, self._ticks.set) if settings.finnhub_api_key else None

        self.alerts = AlertMonitor()
        self.alert_counts = AlertCounts(state.path.parent / "alert_counts.json")  # mode 600
        self.phone = PhoneNotifier(settings, on_status=self._phone_status)
        self.dashboard = DashboardSettings(state.path.parent / "settings.json")  # mode 600
        self.health = health or SourceHealth()
        self.health.register("quotes", "Yahoo prices", settings.quotes_interval)
        self.health.register("movers", "Yahoo movers", settings.movers_interval)
        self.health.register("news", "News feeds", settings.news_interval)
        self.health.register("halts", "Nasdaq halts", settings.halts_interval)
        self.health.register("gaps", "Yahoo pre-market", GAP_INTERVAL)
        if self.filings:
            self.health.register("filings", "SEC filings", settings.filings_interval)
        if self.finnhub:
            self.health.register("finnhub", "Finnhub stream", HEALTH_CHECK_SECONDS)
        self._health_seen: list = []  # what the browsers were last told
        self._tasks: list[asyncio.Task] = []

    async def _call(self, name: str, fn: Callable, *args):
        """A Yahoo call (blocking, so on a thread), through the recorder or player if any."""
        return await self.io.call(name, fn, *args)

    def clock_info(self) -> dict:
        recording = self.io.recording
        return {"now": clock.time(), "speed": clock.speed, "replay": clock.replaying,
                "recording": recording.name if recording else None}

    def snapshot(self) -> dict:
        return {
            "clock": self.clock_info(),
            **self._config(),
            "quotes": self.quotes,
            "movers": self.movers,
            "unusual": self.unusual,
            "news": self._latest_news(),
            "alerts": list(self.alerts.recent),
            "halts": self.halts.latest(),
            "phone": self.phone.status(),
            "rvol_threshold": self.settings.rvol_threshold,
            "thresholds": self._thresholds(),
            "price_source": "finnhub" if self.finnhub else "yahoo",
            "health": self._health(),
            "risk": self.risk,
            "gaps": self.gaps,
            "earnings": self.earnings,
            "gap_min_pct": self.settings.gap_min_pct,
            "ssr": self.ssr,
            "pre_highs": self.pre_highs,
            "halt_info": self.halt_info,
            "day2": self.day2,
            "luld": self.luld_near(),
            "sympathy": self.sympathy,
            "floats": self.floats,
        }

    # --- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        s = self.settings
        self._tasks = [
            asyncio.create_task(self._loop("quotes", s.quotes_interval, self._update_quotes, self._wake_quotes)),
            asyncio.create_task(self._loop("movers", s.movers_interval, self._update_movers)),
            asyncio.create_task(self._loop("news", s.news_interval, self._update_news, self._wake_news)),
            asyncio.create_task(self._loop("halts", s.halts_interval, self._update_halts)),
            asyncio.create_task(self.phone.run()),
            asyncio.create_task(self._watch_health()),
            asyncio.create_task(self._loop("risk", RISK_INTERVAL, self._update_risk, self._wake_risk)),
            asyncio.create_task(self._loop("reactions", REACTIONS_INTERVAL, self._update_reactions)),
            asyncio.create_task(self._recap_loop()),
            asyncio.create_task(self._loop("gaps", GAP_INTERVAL, self._update_gaps, self._wake_gaps)),
            asyncio.create_task(self._gap_brief_loop()),
            asyncio.create_task(self._loop("earnings", EARNINGS_INTERVAL, self._update_earnings, self._wake_earnings)),
            asyncio.create_task(self._loop("tape", TAPE_INTERVAL, self._update_tape, self._wake_tape)),
            asyncio.create_task(self._loop("resumptions", RESUME_TICK, self._update_resumptions)),
            asyncio.create_task(self._fill_offering_risk()),
        ]
        if self.filings:
            self._tasks.append(asyncio.create_task(
                self._loop("filings", s.filings_interval, self._update_filings)))
        if self.finnhub:
            await self.finnhub.set_symbols(self.state.watchlist)
            self._tasks += [
                asyncio.create_task(self.finnhub.run()),
                asyncio.create_task(self._loop("finnhub quotes", s.finnhub_quotes_interval,
                                               self.finnhub.refresh_quotes, self._wake_finnhub)),
                asyncio.create_task(self._flush_ticks()),
            ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.news.aclose()
        await self.sec.aclose()
        await self.halts.aclose()
        await self.phone.aclose()
        self.reactions.close()
        self.io.close()
        if self.finnhub:
            await self.finnhub.aclose()

    # --- polling ----------------------------------------------------------------------------

    async def _loop(self, name: str, interval: float, step: Callable[[], Awaitable[str | None]],
                    wake: asyncio.Event | None = None) -> None:
        """Run step every interval (or sooner when woken). If name is a tracked source, its
        health records the outcome: step returns a note on partial success, raises on failure."""
        while True:
            await self._run_step(name, step)
            # Intervals are market time: a replay at 10x speed polls 10x as often.
            if wake is None:
                await clock.sleep(interval)
                continue
            try:
                await asyncio.wait_for(wake.wait(), clock.real_seconds(interval))
            except TimeoutError:
                pass
            wake.clear()

    async def _run_step(self, name: str, step: Callable[[], Awaitable[str | None]]) -> None:
        try:
            note = await step()
        except Exception as exc:
            if isinstance(exc, SourceError):
                log.warning("%s update failed: %s", name, exc)
            else:
                log.exception("%s update failed", name)
            if name in self.health:
                self.health.fail(name, describe(exc))
        else:
            if name in self.health:
                self.health.ok(name, note)

    # --- health -----------------------------------------------------------------------------

    def _health(self) -> dict:
        # "now" lets the browser show ages without trusting its own clock.
        return {"now": self.health.now(), "sources": self.health.status()}

    async def _check_health(self) -> None:
        if self.finnhub:
            if self.finnhub.connected:
                self.health.ok("finnhub", self.finnhub.error)
            else:
                self.health.fail("finnhub", self.finnhub.error or "not connected yet")
        status = self.health.status()
        seen = [(s["name"], s["state"], s["reason"]) for s in status]
        if seen != self._health_seen:
            self._health_seen = seen
            await self.hub.broadcast({"type": "health", "data": self._health()})
        if self.settings.source_alert_minutes > 0:
            events = self.health.outages(self.settings.source_alert_minutes * 60)
            for e in events:
                log.warning("source %s %s: %s", e["label"], e["kind"], e["reason"] or "")
            self.phone.enqueue([from_outage(e) for e in events], self.state.phone_alerts)

    async def _watch_health(self) -> None:
        while True:
            try:
                await self._check_health()
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(HEALTH_CHECK_SECONDS)

    # --- alerts -----------------------------------------------------------------------------

    async def _publish_alerts(self, fired: list[dict]) -> None:
        """Every alert goes out through here: counted, measured, then shown and sent to the phone
        unless muted or snoozed (app/noise.py), and to the phone up to the daily cap per kind."""
        if not fired:
            return
        self._add_track_records(fired)
        self._track_alerts(fired)  # muted ones too: the panel shows what you'd be missing
        today = clock.today(market.ET)
        day, cap = today.isoformat(), self.settings.phone_cap_per_kind
        shown, notes = [], []
        for e in fired:
            if snoozed(self.state.snoozed, e["symbol"], day):
                self.alert_counts.add(e["kind"], day, phoned=False)
                continue
            if not muted(self.state.alert_mutes, e["kind"], "browser"):
                shown.append(e)
            note = from_alert(e, self.settings.public_url)
            to_phone = (self.phone.channels and self.state.phone_alerts.get(note["category"], False)
                        and not muted(self.state.alert_mutes, e["kind"], "phone")
                        and not (cap and self.alert_counts.phoned(e["kind"], day) >= cap))
            if to_phone:
                notes.append(note)
            self.alert_counts.add(e["kind"], day, phoned=bool(to_phone))
        self.alert_counts.save(today)
        if shown:
            self.alerts.record(shown)
            log.info("alerts: %s", "; ".join(e["message"] for e in shown))
            await self.hub.broadcast({"type": "alerts", "data": shown})
        if notes:
            self.phone.enqueue(notes, self.state.phone_alerts)
        await self.hub.broadcast({"type": "alert_counts", "data": self.alert_counts.summary(today)})

    async def _phone_status(self, status: dict) -> None:
        await self.hub.broadcast({"type": "phone", "data": status})

    # --- watchlist, keywords and rules ------------------------------------------------------

    async def add_symbol(self, symbol: str) -> bool:
        if symbol in self.state.watchlist:
            return False
        self.state.watchlist.append(symbol)
        await self._config_changed()
        await self._watchlist_changed()
        self._wake_news.set()
        return True

    async def remove_symbol(self, symbol: str) -> bool:
        if symbol not in self.state.watchlist:
            return False
        self.state.watchlist.remove(symbol)
        self.yahoo_quotes = [q for q in self.yahoo_quotes if q["symbol"] != symbol]
        await self._config_changed()
        await self._watchlist_changed()
        return True

    async def add_keyword(self, keyword: str) -> bool:
        if keyword.lower() in (k.lower() for k in self.state.keywords):
            return False
        self.state.keywords.append(keyword)
        await self._config_changed()
        return True

    async def remove_keyword(self, keyword: str) -> bool:
        match = next((k for k in self.state.keywords if k.lower() == keyword.lower()), None)
        if match is None:
            return False
        self.state.keywords.remove(match)
        await self._config_changed()
        return True

    async def add_rule(self, symbol: str, kind: str, value: float) -> dict:
        rule = new_rule(symbol, kind, value)
        self.state.alert_rules.append(rule)
        await self._config_changed()
        return rule

    async def remove_rule(self, rule_id: str) -> bool:
        rule = next((r for r in self.state.alert_rules if r["id"] == rule_id), None)
        if rule is None:
            return False
        self.state.alert_rules.remove(rule)
        self.alerts.forget_rule(rule_id)
        await self._config_changed()
        return True

    # --- backup and restore -------------------------------------------------------------------

    def backup(self) -> dict:
        return backup_export(self.state, self.settings, clock.today(market.ET).isoformat())

    def _restore_plan(self, data) -> tuple[dict, dict, dict]:
        # Only secrets can come from environment variables, and a backup never restores one.
        state, settings, notes = backup_read(data)
        plan = {"changes": backup_changes(self.state, self.settings, state, settings), "notes": notes}
        return plan, state, settings

    def restore_preview(self, data) -> dict:
        """What restoring this backup would change, without changing anything."""
        return self._restore_plan(data)[0]

    async def restore(self, data) -> dict:
        plan, state, settings = self._restore_plan(data)
        watchlist = list(self.state.watchlist)
        for key, value in state.items():
            setattr(self.state, key, value)
        if settings:
            await self.update_settings(settings)  # saved to data/settings.json and applied, like Setup
        await self._config_changed()
        if self.state.watchlist != watchlist:
            await self._watchlist_changed()
        log.info("restored a backup: %d changes", len(plan["changes"]))  # never the values
        return plan

    def alert_kinds(self) -> list[dict]:
        """Each kind of alert with how often it fired (today, 7 days), how many went to the phone
        today, its track record and whether it's muted: the Alerts panel's noise control."""
        counts = self.alert_counts.summary(clock.today(market.ET))
        records = self._track_records()
        out = []
        for kind, (label, group) in ALERT_KINDS.items():
            c = counts.get(kind, {"today": 0, "week": 0, "phoned_today": 0})
            g = records.get(TRACKED_ALERTS.get(kind, ""))
            out.append({"kind": kind, "label": label, "group": group, **c,
                        "record": {k: g[k] for k in ("measured", "median_30m", "median_close", "down_share")} if g else None,
                        "browser": not muted(self.state.alert_mutes, kind, "browser"),
                        "phone": not muted(self.state.alert_mutes, kind, "phone")})
        return out

    async def set_alert_mute(self, kind: str, browser: bool | None, phone: bool | None) -> None:
        """browser/phone: True shows / sends that kind, False mutes it, None leaves it."""
        entry = dict(self.state.alert_mutes.get(kind, {}))
        for where, on in (("browser", browser), ("phone", phone)):
            if on is not None:
                entry[where] = not on
        entry = {k: v for k, v in entry.items() if v}
        if entry:
            self.state.alert_mutes[kind] = entry
        else:
            self.state.alert_mutes.pop(kind, None)
        await self._config_changed()

    async def snooze(self, symbol: str) -> str:
        """No alerts about symbol for the rest of today (ET). Returns the day."""
        day = clock.today(market.ET).isoformat()
        self.state.snoozed = {s: d for s, d in self.state.snoozed.items() if d == day}  # yesterday's are over
        self.state.snoozed[symbol] = day
        await self._config_changed()
        return day

    async def unsnooze(self, symbol: str) -> bool:
        if self.state.snoozed.pop(symbol, None) is None:
            return False
        await self._config_changed()
        return True

    async def set_unusual_alerts(self, enabled: bool) -> None:
        self.state.unusual_alerts = enabled
        await self._config_changed()

    async def set_rotation_alerts(self, enabled: bool) -> None:
        self.state.rotation_alerts = enabled
        await self._config_changed()

    async def set_breakout_alerts(self, enabled: bool) -> None:
        self.state.breakout_alerts = enabled
        await self._config_changed()

    async def set_halt_alerts(self, mode: str) -> None:
        self.state.halt_alerts = mode
        await self._config_changed()

    async def set_phone_alerts(self, categories: dict[str, bool]) -> None:
        self.state.phone_alerts.update(categories)
        await self._config_changed()

    def _thresholds(self) -> dict:
        """The settings the browser needs to draw badges the way the server alerts on them."""
        s = self.settings
        return {"rvol_threshold": s.rvol_threshold, "rotation_multiple": s.rotation_multiple,
                "sympathy_min_pct": s.sympathy_min_pct, "sympathy_lead_pct": s.sympathy_lead_pct}

    def _config(self) -> dict:
        return self.state.to_dict()

    async def _config_changed(self) -> None:
        self.state.save()
        await self.hub.broadcast({"type": "config", "data": self._config()})

    async def _watchlist_changed(self) -> None:
        self._wake_quotes.set()
        self._wake_earnings.set()
        self._wake_risk.set()
        self._wake_tape.set()
        if self.finnhub:
            await self.finnhub.set_symbols(self.state.watchlist)
            self._wake_finnhub.set()

    # --- ticker detail ----------------------------------------------------------------------

    async def industry_movers(self, symbol: str) -> dict:
        """Today's biggest gainers in symbol's industry (Yahoo's screen), for the ticker panel.
        Industry None when Yahoo has none for it (ETFs, funds, some foreign stocks) or its company
        data can't be read; raises only if the screen itself fails."""
        industry = self.industries.get(symbol)
        if not industry:
            try:
                industry = (await self._call("fetch_profile", market.fetch_profile, symbol) or {}).get("industry")
            except Exception as exc:
                log.info("industry of %s unknown: %s", symbol, describe(exc))
        if not industry:
            return {"industry": None, "movers": []}
        movers = await self._call("fetch_industry_movers", market.fetch_industry_movers, industry)
        return {"industry": industry, "movers": movers}

    async def ticker_detail(self, symbol: str, range_: str = "1d") -> dict | None:
        """Yahoo profile + intraday bars + latest known quote for one symbol. News, halts and
        alerts already stream to the browser, which filters them itself so they stay live.
        None if Yahoo has neither a profile nor bars for the symbol."""
        profile, chart = await asyncio.gather(
            self._call("fetch_profile", market.fetch_profile, symbol),
            self._call("fetch_chart", market.fetch_chart, symbol, range_),
            return_exceptions=True,
        )
        errors = []
        if isinstance(profile, BaseException):
            log.warning("profile for %s failed: %s", symbol, profile)
            profile, errors = {}, errors + ["profile"]
        if isinstance(chart, BaseException):
            log.warning("chart for %s failed: %s", symbol, chart)
            chart, errors = {"range": range_, "bars": []}, errors + ["chart"]
        if not errors and not profile and not chart["bars"]:
            return None

        rows = [*self.quotes, *(r for rows in self.movers.values() for r in rows)]
        quote = next((r for r in rows if r["symbol"] == symbol and r.get("price") is not None), None)
        if quote is None and range_ == "1d":
            quote = market.quote_from_bars(symbol, chart["bars"], profile)
        return {"symbol": symbol, "profile": profile, "chart": chart, "quote": quote, "errors": errors,
                "luld": self.luld.get(symbol)}  # estimated bands, regular hours

    # --- your data -------------------------------------------------------------------------

    @property
    def _recordings(self) -> tuple:
        """The recordings folder, and the file being written (if recording)."""
        active = self.io.recording if isinstance(self.io, Recorder) else None
        return self.state.path.parent / "recordings", active

    def data_inventory(self) -> list[dict]:
        folder, active = self._recordings
        return [
            state_item(self.state.path, self.state.watchlist, self.state.keywords, self.state.alert_rules),
            settings_item(self.dashboard.path),
            reactions_item(self.reactions.path, self.reactions.stats(), self.settings.reactions_keep_days),
            recordings_item(folder, self.settings.record_keep, active),
            tls_item(TLS_DIR, tls_status(TLS_DIR)),
            alert_counts_item(self.alert_counts.path,
                              sum(c["week"] for c in self.alert_counts.summary(clock.today(market.ET)).values())),
            log_item(),
        ]

    async def delete_data(self, store: str) -> None:
        if store not in DELETABLE:
            raise SettingsError(f"{store} can't be deleted here")
        if store == "reactions":
            self.reactions.clear()
            self._reaction_retry.clear()
        elif store == "recordings":
            delete_recordings(*self._recordings)
        elif store == "alert_counts":
            self.alert_counts.clear()
        elif store == "dashboard_settings":
            self.dashboard.path.unlink(missing_ok=True)
            fresh = self.dashboard.effective({})  # back to config.toml, environment and defaults
            apply(self.settings, fresh, [n for n in EDITABLE if n != "finnhub_api_key"])
            self.sec.set_user_agent(self.settings.sec_user_agent)
            await self.hub.broadcast({"type": "phone", "data": self.phone.status()})
        log.info("deleted from Your data: %s", store)  # what, never the contents

    # --- settings from the dashboard --------------------------------------------------------

    async def update_settings(self, changes: dict[str, object]) -> dict:
        """Validate, save to data/settings.json and apply what can be applied live. An empty
        value removes the dashboard's value, falling back to config.toml or the default."""
        stored = self.dashboard.load()
        for name, value in changes.items():
            if self.dashboard.source(name, stored) == "environment":
                raise SettingsError(f"{name} is set by the environment variable {name.upper()}; change it there")
            if value is None or value == "":
                stored.pop(name, None)
            else:
                stored[name] = validate(name, value)
        try:
            fresh = self.dashboard.effective(stored)
        except (TypeError, ValueError) as exc:
            raise SettingsError(f"Not saved: {exc}") from None
        self.dashboard.save(stored)
        restart = apply(self.settings, fresh, list(changes))
        if "sec_user_agent" in changes:
            self.sec.set_user_agent(self.settings.sec_user_agent)
        log.info("settings changed from the dashboard: %s", ", ".join(sorted(changes)))  # names only
        await self.hub.broadcast({"type": "phone", "data": self.phone.status()})
        await self.hub.broadcast({"type": "thresholds", "data": self._thresholds()})
        return {"saved": sorted(changes), "restart_required": restart}
