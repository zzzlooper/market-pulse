"""Scenario kit: write a made-up trading day as a recording, replay it through the real engine
one step at a time, and collect what you would see: browser messages, phone notes, the alert
log, tracked reactions and the recap.

    day = Day(tmp_path)
    day.movers("09:45", gainers={"ACME": 45})
    day.filing("10:02", "424B5", "ACME")
    day.profile("ACME", "high")
    run = Run(day, watchlist=["BOLT"])
    run.steps("09:31", "09:46", "10:05")
    assert run.phone_titles() == ["ACME · offering from shelf"]

Everything is fictional: invented companies and tickers, no people at all (insider filings
carry only a role), placeholder contact details. Nothing leaves the machine: the replay never
uses the network and the phone is never called; notes are read from its queue.

The engine's loops are not running. Each step freezes the market clock and runs one round:
quotes, movers, news, halts, filings, pre-market gaps, earnings, tape (SSR, floats), risk,
reaction measuring, health. Sources are polled in
that order, so a filing at a step sees the movers of the same step.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from app.clock import clock
from app.config import Settings, UserState
from app.dilution import CASH, CONCEPT_URL, OPERATING_CASH_FLOW, SHARES, SUBMISSIONS_URL
from app.engine import Engine
from app.filings import FEED_TYPES, FEED_URL
from app.halts import HALTS_URL
from app.health import SourceHealth
from app.market import ET
from app.replay import Player, Recorder, url_key
from app.sec import TICKERS_URL

DAY = date(2026, 10, 13)  # a Tuesday
OPEN = "09:00"            # default start of the recording; first step usually 09:31
WIRE = "https://wire.example.test/rss"
CONTACT = "market-pulse/scenarios ops@example.org"
PHONE_TOPIC = "mp-scenario-topic-000000"

# Invented listed companies: ticker -> (name, CIK).
COMPANIES = {
    "ACME": ("Acme Rocket Corp", "0000000101"),
    "BOLT": ("Bolt Battery Inc", "0000000102"),
    "CRUX": ("Crux Biotherapeutics Inc", "0000000103"),
    "DYNA": ("Dyna Robotics Corp", "0000000104"),
    "ECHO": ("Echo Mining Ltd", "0000000105"),
    "FERN": ("Fern Foods Inc", "0000000106"),
}


def ts(hhmm: str, day: date = DAY) -> float:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), ET).timestamp()


def get(url: str) -> str:
    """The recording key for a GET of url, made the way the recorder makes it."""
    return url_key(httpx.Request("GET", url))


def iso(hhmm: str) -> str:
    return datetime.fromtimestamp(ts(hhmm), ET).isoformat()


class Day:
    """A made-up trading day, built up source by source, then written as a recording."""

    def __init__(self, folder: Path, start: str = OPEN):
        self.folder = folder
        self.start = start  # when the recording begins (04:00 for a pre-market day)
        self._records: list[tuple[float, str, dict]] = []
        self._feeds: dict[str, list[tuple[float, str]]] = {t: [] for t in FEED_TYPES}  # type -> (time, entry)
        self._wire: list[tuple[float, str]] = []            # (time, RSS item)
        self._wire_outages: list[tuple[float, float]] = []  # (from, until): the wire answers 503
        self._halts: dict[tuple[str, str], list[tuple[float, str]]] = {}  # (symbol, halted) -> versions
        self._accessions = 0
        self._quoted = False
        self.record(self.start, get(TICKERS_URL), self._json({
            str(i): {"cik_str": int(cik), "ticker": t} for i, (t, (_, cik)) in enumerate(COMPANIES.items())}))
        self.movers(self.start)

    # --- raw ---------------------------------------------------------------------------------

    def record(self, hhmm: str, key: str, payload: dict) -> None:
        self._records.append((ts(hhmm), key, payload))

    @staticmethod
    def _json(data, status: int = 200) -> dict:
        return {"s": status, "ct": "application/json", "b": json.dumps(data)}

    # --- Yahoo --------------------------------------------------------------------------------

    def quotes(self, hhmm: str, low: dict | None = None, volume: dict | None = None, session: date = DAY,
               high: dict | None = None, **change_pct: float) -> None:
        """Watchlist prices: each symbol at 10.00 × (1 + change%), with ordinary volume (or
        volume[symbol]). low[symbol] / high[symbol]: the session's low / high so far, from a 10.00
        close. session: the day the quote describes (before the open, Yahoo's latest is the
        previous session)."""
        self._quoted = True
        rows = [{"symbol": s, "price": round(10 * (1 + p / 100), 4), "prev_close": 10.0, "change": p / 10,
                 "change_pct": p, "volume": (volume or {}).get(s, 1_000_000), "avg_volume": 1_000_000, "rvol": 1.0,
                 "low": (low or {}).get(s), "high": (high or {}).get(s), "session": session.isoformat()}
                for s, p in change_pct.items()]
        self.record(hhmm, f"call fetch_quotes {json.dumps([sorted(change_pct)])}", {"r": rows})

    def movers(self, hhmm: str, gainers: dict | None = None, small_caps: dict | None = None,
               rvol: dict | None = None, volume: dict | None = None) -> None:
        def rows(entries):
            return [{"symbol": s, "name": COMPANIES.get(s, (s,))[0], "price": 10 * (1 + p / 100), "change_pct": p,
                     "volume": (volume or {}).get(s, 5_000_000), "avg_volume": 1_000_000, "rvol": (rvol or {}).get(s, 1.0),
                     "prev_close": 10.0, "session": DAY.isoformat()}
                    for s, p in (entries or {}).items()]
        self.record(hhmm, "call fetch_movers []", {"r": {
            "gainers": rows(gainers), "losers": [], "active": [], "small_caps": rows(small_caps)}})

    def chart(self, symbol: str, price_at, recorded: str | None = None) -> None:
        """1-minute bars for the whole day (04:00–20:00), price_at("HH:MM") -> price."""
        recorded = recorded or self.start
        bars, t = [], ts("04:00")
        while t < ts("20:00"):
            p = price_at(datetime.fromtimestamp(t, ET).strftime("%H:%M"))
            bars.append({"t": int(t), "o": p, "h": p, "l": p, "c": p, "v": 1000})
            t += 60
        self.record(recorded, f'call fetch_chart {json.dumps([symbol, "1d"])}',
                    {"r": {"range": "1d", "interval": "1m", "bars": bars}})

    def daily(self, hhmm: str, **sessions: list[tuple[str, float, float]]) -> None:
        """The last sessions' daily bars per symbol, as Yahoo answers the SSR lookup:
        symbol=[("YYYY-MM-DD", low, close), …], oldest first."""
        rows = [{"symbol": s, "days": [{"date": d, "low": lo, "close": c} for d, lo, c in days]}
                for s, days in sessions.items()]
        self.record(hhmm, f"call fetch_daily {json.dumps([sorted(sessions)])}", {"r": rows})

    def floats(self, hhmm: str, **shares: int | None) -> None:
        """Shares in the public float per symbol, as Yahoo's company data gives them."""
        rows = [{"symbol": s, "float_shares": n} for s, n in shares.items()]
        self.record(hhmm, f"call fetch_floats {json.dumps([sorted(shares)])}", {"r": rows})

    def premarket(self, hhmm: str, pre_high: dict | None = None, **gaps: tuple[float, int]) -> None:
        """Pre-market gap rows as the scanner's Yahoo download returns them:
        symbol=(gap %, minutes with trades), from a 10.00 close. pre_high[symbol]: the highest
        pre-market trade (default: the latest price)."""
        rows = [{"symbol": s, "prev_close": 10.0, "price": round(10 * (1 + g / 100), 4), "gap_pct": g,
                 "pre_minutes": m, "last_trade": int(ts(hhmm)), "at_open": False,
                 "pre_high": (pre_high or {}).get(s, round(10 * (1 + g / 100), 4))} for s, (g, m) in gaps.items()]
        self.record(hhmm, f"call fetch_premarket {json.dumps([sorted(gaps)])}", {"r": rows})

    def earnings(self, hhmm: str, **reports: tuple[str, str, bool]) -> None:
        """Next earnings per symbol as the Yahoo lookup returns them:
        symbol=("YYYY-MM-DD", "HH:MM" ET, estimated?)."""
        from app.earnings import when
        rows = []
        for symbol, (day, at, estimate) in reports.items():
            moment = datetime.combine(date.fromisoformat(day), time(*(int(x) for x in at.split(":"))), ET)
            rows.append({"symbol": symbol, "at": int(moment.timestamp()), "date": day, "when": when(moment),
                         "estimate": estimate})
        self.record(hhmm, f"call fetch_earnings {json.dumps([sorted(reports)])}", {"r": rows})

    # --- SEC ----------------------------------------------------------------------------------

    def filing(self, hhmm: str, form: str, ticker: str, items: list[tuple[str, str]] = (),
               document: str | None = None, text: str | None = None) -> str:
        """A filing by an invented company appearing in EDGAR's feed at hhmm. Returns its
        accession number. document: the XML of a Form 4 / 13D, served as its .txt; text: an
        offering's or 8-K's own words (its first pages), served as its .txt."""
        name, cik = COMPANIES[ticker]
        self._accessions += 1
        acc = f"{cik}-26-{self._accessions:06d}"
        folder = acc.replace("-", "")
        summary = "&lt;b&gt;Filed:&lt;/b&gt; 2026-10-13" + "".join(f"&lt;br&gt;Item {i}: {d}" for i, d in items)
        role = "Issuer" if form == "4" else "Subject" if form.startswith("SCHEDULE 13D") else "Filer"
        entry = (f'<entry><title>{form} - {name} ({cik}) ({role})</title>'
                 f'<link rel="alternate" type="text/html" '
                 f'href="https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{acc}-index.htm"/>'
                 f'<summary type="html">{summary}</summary><updated>{iso(hhmm)}</updated>'
                 f'<id>urn:tag:sec.gov,2008:accession-number={acc}</id></entry>')
        feed_type = next(t for t in FEED_TYPES if form.startswith(t)) if form != "4" else "4"
        self._feeds[feed_type].append((ts(hhmm), entry))
        if document is not None or text is not None:
            body = f"<XML>\n{document}\n</XML>" if document is not None else text
            self.record(hhmm, get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/{acc}.txt"),
                        {"s": 200, "ct": "text/plain", "b": body})
        return acc

    def insider_trade(self, hhmm: str, ticker: str, code: str, shares: int, price: float, owned_after: int,
                      role_title: str = "Chief Executive Officer") -> str:
        """A Form 4 with one open-market trade by an officer. No person is named."""
        name, cik = COMPANIES[ticker]
        doc = (f"<ownershipDocument><issuer><issuerCik>{cik}</issuerCik><issuerName>{name}</issuerName>"
               f"<issuerTradingSymbol>{ticker}</issuerTradingSymbol></issuer>"
               f"<reportingOwner><reportingOwnerRelationship><isOfficer>1</isOfficer>"
               f"<officerTitle>{role_title}</officerTitle></reportingOwnerRelationship></reportingOwner>"
               f"<aff10b5One>0</aff10b5One><nonDerivativeTable><nonDerivativeTransaction>"
               f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
               f"<transactionAmounts><transactionShares><value>{shares}</value></transactionShares>"
               f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare></transactionAmounts>"
               f"<postTransactionAmounts><sharesOwnedFollowingTransaction><value>{owned_after}</value>"
               f"</sharesOwnedFollowingTransaction></postTransactionAmounts>"
               f"</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>")
        return self.filing(hhmm, "4", ticker, document=doc)

    def profile(self, ticker: str, level: str, offering_text: str | None = None) -> None:
        """SEC history and financials that score `level` (high or low) for dilution risk.
        offering_text: the document of the newest offering (a high profile's 424B5 of Aug 1)."""
        _, cik = COMPANIES[ticker]
        if level == "high":
            # Pending S-1, effective shelf, two offerings, private sales, ~2 months of cash,
            # share count tripled.
            rows = [("S-1", "2026-10-01", ""), ("424B5", "2026-08-01", ""), ("8-K", "2026-07-15", "3.02,9.01"),
                    ("424B5", "2026-03-01", ""), ("EFFECT", "2026-01-20", ""), ("S-3", "2026-01-10", "")]
            cash, burn, shares = 2_000_000, -6_000_000, (10_000_000, 30_000_000)
        elif level == "low":
            rows = [("10-Q", "2026-08-01", ""), ("8-K", "2026-07-30", "2.02,9.01")]
            cash, burn, shares = 900_000_000, 50_000_000, (100_000_000, 99_000_000)
        else:
            raise ValueError(level)
        self.record(self.start, get(SUBMISSIONS_URL.format(cik=cik)), self._json({
            "cik": cik, "filings": {"files": [], "recent": {
                "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows], "items": [r[2] for r in rows],
                "accessionNumber": [f"{cik}-26-9{i:05d}" for i in range(len(rows))],
                "primaryDocument": ["offering.htm" if offering_text and r == rows[1] else "" for r in rows]}}}))
        if offering_text:
            folder = f"{cik}-26-9{1:05d}".replace("-", "")
            self.record(self.start, get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{folder}/offering.htm"),
                        {"s": 200, "ct": "text/html", "b": offering_text})

        def concept(spec, unit, facts):
            taxonomy, name = spec[0]
            self.record(self.start, get(CONCEPT_URL.format(cik=cik, taxonomy=taxonomy, concept=name)),
                        self._json({"units": {unit: facts}}))
        concept(CASH, "USD", [{"end": "2026-06-30", "val": cash, "filed": "2026-08-01"}])
        concept(OPERATING_CASH_FLOW, "USD", [{"start": "2026-01-01", "end": "2026-06-30", "val": burn}])
        concept(SHARES, "shares", [{"end": "2025-08-01", "val": shares[0]}, {"end": "2026-08-01", "val": shares[1]}])

    # --- news and halts --------------------------------------------------------------------------

    def headline(self, hhmm: str, title: str) -> None:
        published = format_datetime(datetime.fromtimestamp(ts(hhmm), ET))
        self._wire.append((ts(hhmm), (f"<item><title>{title}</title><link>https://wire.example.test/n/{len(self._wire)}</link>"
                                      f"<description>{title}</description><pubDate>{published}</pubDate></item>")))

    def wire_down(self, start: str, end: str) -> None:
        """The news wire answers 503 from start until end."""
        self._wire_outages.append((ts(start), ts(end)))

    def halt(self, hhmm: str, symbol: str, code: str = "LUDP", halted: str | None = None,
             resumes: str | None = None) -> None:
        """A halt appearing in Nasdaq's feed at hhmm (halted at `halted`, default the same)."""
        halted = halted or hhmm
        resume = (f"<ndaq:ResumptionDate>10/13/2026</ndaq:ResumptionDate>"
                  f"<ndaq:ResumptionTradeTime>{resumes}:00</ndaq:ResumptionTradeTime>") if resumes else ""
        # The same halt again (e.g. with a resumption time) replaces the earlier version.
        self._halts.setdefault((symbol, halted), []).append((ts(hhmm), (
            f"<item><title>{symbol}</title><ndaq:HaltDate>10/13/2026</ndaq:HaltDate>"
            f"<ndaq:HaltTime>{halted}:00</ndaq:HaltTime><ndaq:IssueSymbol>{symbol}</ndaq:IssueSymbol>"
            f"<ndaq:ReasonCode>{code}</ndaq:ReasonCode>{resume}</item>")))

    # --- writing ----------------------------------------------------------------------------------

    def _cumulative(self, key: str, items: list[tuple[float, str]], wrap) -> None:
        """Record a feed's full contents at the start and each time an item appears."""
        times = sorted({ts(self.start), *(t for t, _ in items)})
        for t in times:
            shown = [text for when, text in sorted(items, key=lambda i: -i[0]) if when <= t]
            self._records.append((t, key, wrap(shown)))

    def build(self, watchlist: list[str]) -> Path:
        if not self._quoted:  # flat prices, so Yahoo doesn't look down
            self.quotes(self.start, **dict.fromkeys(watchlist, 0.0))
        def atom(entries):
            return {"s": 200, "ct": "application/atom+xml",
                    "b": '<feed xmlns="http://www.w3.org/2005/Atom">' + "".join(entries) + "</feed>"}
        for feed_type, entries in self._feeds.items():
            key = get(FEED_URL.format(type=quote(feed_type), start=0))
            self._cumulative(key, entries, atom)

        # The wire, recorded whenever an item appears or an outage starts or ends.
        changes = {ts(self.start), *(t for t, _ in self._wire), *(t for span in self._wire_outages for t in span)}
        for t in sorted(changes):
            if any(start <= t < end for start, end in self._wire_outages):
                payload = {"s": 503, "ct": "text/plain", "b": ""}
            else:
                items = [text for when, text in sorted(self._wire, key=lambda i: -i[0]) if when <= t]
                payload = {"s": 200, "ct": "application/rss+xml",
                           "b": "<rss><channel>" + "".join(items) + "</channel></rss>"}
            self._records.append((t, get(WIRE), payload))

        for t in sorted({ts(self.start), *(t for versions in self._halts.values() for t, _ in versions)}):
            current = [max((v for v in versions if v[0] <= t), default=(0, ""))[1] for versions in self._halts.values()]
            self._records.append((t, get(HALTS_URL), {
                "s": 200, "ct": "application/rss+xml",
                "b": '<rss xmlns:ndaq="http://www.nasdaqtrader.com/"><channel>' + "".join(current) + "</channel></rss>"}))

        # Write through the real recorder, so the scenarios exercise the recording format too.
        clock.freeze(ts(self.start))
        recorder = Recorder(self.folder / "recordings", {"watchlist": watchlist}, keep=5)
        for t, key, payload in sorted(self._records, key=lambda r: r[0]):
            clock.freeze(t)
            recorder.record(key, dict(payload))
        recorder.close()
        clock.reset()
        return recorder.recording


class Hub:
    def __init__(self):
        self.messages: list[dict] = []

    async def broadcast(self, msg: dict) -> None:
        self.messages.append(msg)


class Run:
    """The engine replaying a Day, stepped by hand."""

    def __init__(self, day: Day, watchlist: list[str], phone: dict[str, bool] | None = None, **settings):
        self.recording = day.build(watchlist)
        self.player = Player(self.recording)
        state = UserState(day.folder / "data" / "state.json")
        state.watchlist = list(watchlist)
        if phone:
            state.phone_alerts.update(phone)
        self.hub = Hub()
        clock.freeze(ts(day.start))  # data-source health starts counting from the recorded morning
        base = {"feeds": {"Wire": WIRE}, "ticker_news": False, "sec_user_agent": CONTACT,
                "ntfy_topic": PHONE_TOPIC, "source_alert_minutes": 5}
        self.engine = Engine(Settings(**{**base, **settings}), state, self.hub,
                             health=SourceHealth(clock.time), io=self.player)
        self.phone: list[dict] = []
        self.now = ""

    async def _step(self, hhmm: str) -> None:
        e = self.engine
        clock.freeze(ts(hhmm))
        self.now = hhmm
        for name, update in (("quotes", e._update_quotes), ("movers", e._update_movers), ("news", e._update_news),
                             ("halts", e._update_halts), ("filings", e._update_filings), ("gaps", e._update_gaps),
                             ("earnings", e._update_earnings), ("tape", e._update_tape)):
            await e._run_step(name, update)
        await e._update_risk()
        while not e._risk_queue.empty():
            await e._fill_one_offering_risk(*e._risk_queue.get_nowait())
        await e._update_reactions()
        await e._check_health()
        while not e.phone._queue.empty():  # what would go to the phone; never sent
            self.phone.append({**e.phone._queue.get_nowait(), "at": hhmm})

    def steps(self, *times: str) -> Run:
        async def go():
            for hhmm in times:
                await self._step(hhmm)
        asyncio.run(go())
        return self

    def brief(self, hhmm: str = "09:15") -> bool:
        """Run the pre-market brief at hhmm; True if it was queued for the phone."""
        clock.freeze(ts(hhmm))

        async def go():
            sent = await self.engine._send_gap_brief()
            while not self.engine.phone._queue.empty():
                self.phone.append({**self.engine.phone._queue.get_nowait(), "at": hhmm})
            return sent
        return asyncio.run(go())

    def recap(self, hhmm: str = "16:30") -> dict:
        clock.freeze(ts(hhmm))
        return asyncio.run(self.engine.recap_note())

    def close(self) -> None:
        async def go():
            e = self.engine
            await e.news.aclose()
            await e.sec.aclose()
            await e.halts.aclose()
            await e.phone.aclose()
            e.reactions.close()
        asyncio.run(go())
        clock.reset()

    # --- what you'd see ---------------------------------------------------------------------------

    def phone_titles(self, category: str | None = None) -> list[str]:
        return [n["title"] for n in self.phone if category is None or n["category"] == category]

    def browser(self, kind: str) -> list:
        """Every item broadcast as `kind` (news items, alerts…), flattened."""
        out = []
        for m in self.hub.messages:
            if m["type"] == kind:
                out += m["data"] if isinstance(m["data"], list) else [m["data"]]
        return out

    def news(self, title_part: str) -> dict:
        return next(n for n in self.browser("news") if title_part in n["title"])

    def alert_log(self) -> list[str]:
        return [a["message"] for a in reversed(list(self.engine.alerts.recent))]
