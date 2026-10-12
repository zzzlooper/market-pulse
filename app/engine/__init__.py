"""The engine: background polling loops and the live snapshot they maintain.

One Engine object, assembled from a mixin per area so each file stays about one thing:

    core.py       lifecycle, the polling-loop runner, health, snapshot, user actions, settings
    prices.py     watchlist quotes, Finnhub ticks, movers, unusual volume, price alerts
    feeds.py      news, SEC filings and trading halts
    risk.py       dilution-risk summaries for the watched and moving stocks
    catalysts.py  catalyst reaction tracking (what's due, measuring, the report)
    daily.py      the end-of-day recap

Each mixin sets up the state it owns in its own _init_* method, called from Engine.__init__.
"""
from .core import Engine, Hub

__all__ = ["Engine", "Hub"]
