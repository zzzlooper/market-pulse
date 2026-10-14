"""The engine: background polling loops and the live snapshot they maintain.

One Engine object, assembled from a mixin per area so each file stays about one thing:

    core.py       lifecycle, the polling-loop runner, health, snapshot, user actions, settings
    prices.py     watchlist quotes, Finnhub ticks, movers, unusual volume, price alerts
    feeds.py      news, SEC filings and trading halts
    risk.py       dilution-risk summaries for the watched and moving stocks
    catalysts.py  catalyst reaction tracking (what's due, measuring, the report)
    daily.py      the end-of-day recap
    gaps.py       the pre-market gap scanner and its 09:15 brief
    earnings.py   the watchlist's next earnings dates (ER badges, brief and recap lines)
    tape.py       SSR, float rotation, breakouts, day-2 runners, LULD bands and sympathy moves
    resumptions.py  halt resumptions: the heads-up a minute before, and how trading resumed

Each mixin sets up the state it owns in its own _init_* method, called from Engine.__init__.
"""
from .core import Engine, Hub

__all__ = ["Engine", "Hub"]
