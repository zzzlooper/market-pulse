# The dashboard

What's on screen: the tables, the ticker panel, the game plan, alerts, halts and sentiment.
The signals and filings are in [signals.md](signals.md). Back to the [README](../README.md).


| Panel | Source | Refresh |
|---|---|---|
| Watchlist: price, % change, volume, RVOL | Yahoo daily bars (`yfinance.download`); prices from Finnhub if configured | 15 s (Finnhub: streaming) |
| Top movers: gainers, losers, most active, small caps | Yahoo predefined screeners | 60 s |
| Unusual volume | Watchlist plus every screener result with RVOL ≥ threshold | with the above |
| News | SEC 8-K filings, GlobeNewswire, PR Newswire, Yahoo per-ticker RSS | 30 s |
| SEC filings (in the news feed) | EDGAR current filings: offerings, Form 4, Schedule 13D, 8-K | 30 s |
| Dilution risk (ticker detail) | SEC filing history and XBRL financials (data.sec.gov) | on open, cached 6 h |

- **RVOL** (relative volume) is today's volume divided by the 10-day average volume, scaled
  by how much of the 9:30–16:00 ET session has elapsed. Scaling linearly over-flags stocks
  near the open, because real intraday volume is heaviest at the open and close.
- **Tickers on news** come from patterns like `(NASDAQ: ACME)` and `$ACME`, from the SEC
  CIK→ticker map for filings, and from the feed itself for Yahoo per-ticker news.
- **Alerts** fire for new headlines that mention a watchlist ticker or match a keyword.
  Items already in a feed when the app starts, or when you add a ticker, don't alert.
- **Ticker detail** — click any ticker anywhere (watchlist, movers, news, halts, alert log) to
  open a side panel with everything about it:
  - an intraday chart: 1 day of 1-minute bars including pre/post-market (shaded), or 5 days
    of 5-minute bars. It has a previous-close line and volume bars, news dots coloured by
    sentiment, and red bands where the stock was halted. Hover for the bar's OHLC, volume,
    change and any headlines from that minute, and the price under the pointer.
  - lines on the chart for your *above*/*below* rules on the stock (amber, dashed), its warrant
    exercise prices (orange) and offering prices (blue), and today's pre-market high (1 day).
    Levels a little outside the day's range widen it so they show; far-away ones stay off it
    (they're listed under *Dilution risk*).
  - **click or tap the chart to set an alert at that price**: it offers *above* when the price
    is below it and *below* when it's above, and sets it with one more click. A stock not on
    the watchlist is added first, since rules cover the watchlist. The rule then shows as a
    line, and in the panel's alert list where it can be removed.
  - key stats: open, gap, pre-market high (green once the price is above it), day and
    52-week range, volume vs average, RVOL, market cap, float, short % of float, days to
    cover, P/E, beta and next earnings date.
  - the ticker's news, halts, alert rules (add or remove them here) and fired alerts. These
    update live while the panel is open. Chart and stats refresh every 30 s.
  - **Dilution risk**: *high*, *medium*, *low* or *unknown*, with every reason and its points,
    from SEC data only (`app/dilution.py`):
    - filing history: a pending S-1 (+2), an active shelf (+1), offerings in the last 12 months
      (+1, or +2 for two or more), private share sales (8-K 3.02, +1), a delisting notice (+1);
    - warrants, from the first pages of the 3 newest offerings and private sales of the past
      year: sold with an offering (+1), and outstanding warrants worth 10% or more of the shares
      (+1; smaller overhangs are noted without points). The panel's *Warrants* figure shows the
      outstanding shares and their average exercise price, with the filing it came from. Below
      the figures, the price levels from the filings (warrant exercise prices and offering
      prices) are listed highest first, with the current price between them and how far each
      is (see *Warrants in the money* and *Offering prices*);
    - XBRL financials: cash, monthly operating burn and the months of cash left at that rate
      (+2 under 6 months, +1 under 12; positive cash flow −1), and the share count against a
      year earlier (+2 for 50% more, +1 for 20%).
    - 4 points or more is *high*, 2–3 *medium*. Without recent financials (ETFs, trusts, many
      foreign filers) a quiet history is *unknown*, not *low*. The dilution-related filings of
      the last 2 years are listed with links.
    - The financials lag by up to a quarter, and SEC share counts aren't split-adjusted: a
      count from before a reverse split is marked. It's a starting point for your own reading,
      not a verdict.
    - Large, well-known issuers (an automatic S-3ASR shelf) mostly sell debt, so for them
      offerings and the shelf score nothing and the share count is what counts.
    - A **DIL** badge next to a ticker in every table marks high (red) or medium (amber) risk;
      hover it for the reasons. Profiles are kept fresh (every 6 hours) for the watchlist and
      the gainers and small-cap lists (`risk_movers`, at most `risk_max_symbols` = 60), fetched
      one at a time so news and filings aren't held up.
  - **Warrants in the money**: once the price reaches a warrant's exercise price, holders can
    exercise at a profit and sell the new shares, which often caps a run. The levels come from
    the same filings as the dilution profile, at most 6 per stock: warrants sold in an offering
    or private sale (a series once, at its newest stated price) and the outstanding warrants'
    average price. Pre-funded warrants (priced at next to nothing) and warrants an S-1 or F-1
    only proposes (not sold yet, priced provisionally) are left out.
    - A **WAR** badge next to a ticker, in every table and the panel's price line, marks a
      price at or above a level; hover it for which warrants, how many shares and the filing.
    - An alert fires when a watchlist stock or a mover **crosses up** through a level, e.g.
      *CRUX crossed $11.00 (now $11.20), the Series C warrants' exercise price: 2M shares can
      now be exercised at a profit (per the 424B5 of Aug 1, 2026)*. Like the price rules, the
      first reading only records a baseline, and each level fires at most once a day. Watchlist
      stocks go by their own quotes (real-time with Finnhub), movers by the screener's price.
      They're sent to the phone with the price alerts.
    - Profiles exist for the stocks dilution risk covers (watchlist, gainers, small caps), so
      those are the ones watched. Warrant terms can be adjusted later (resets, reverse splits)
      in filings not read here; check the latest before trading on a level.
  - **Offering prices**: what a recent offering or private placement sold at, read from the
    same first pages ("public offering price of $2.10 per share", "purchase price of $2.00 per
    share", "US$1.44 per Unit"). Below it, the offering's buyers are at a loss; stocks often
    trade back to it, and a break below it is the deal "breaking". Assumed prices (an S-1's
    illustration), registration statements and aggregate amounts are left out. The news item
    of a priced offering starts with *Priced at $2.10 per share*.
    - An alert fires when a watchlist stock or a mover **falls below** an offering price
      (*… the deal's buyers are now under water*) and when it **climbs back above** it; each
      direction at most once a day, the first reading a baseline. Sent with the price alerts.
  - *Add to watchlist*, *Filter news* (the old click behaviour), and the company description.
  - Esc or a click outside closes it. `http://localhost:8000/#ticker=AAPL` opens straight to
    a ticker. Times in the panel are ET, to match the chart.
  - Data comes from Yahoo and is delayed like the rest. Company facts are cached for 10
    minutes and bars for 30 s.
- **Game plan** (*Plan* in the header, or `http://localhost:8000/#plan`; `app/plan.py`): today's
  gappers in one list, those with a catalyst (a headline or a filing since yesterday's close)
  first, then by the size of the gap. Each card has the gap and price, minutes traded
  pre-market, the catalyst, float and market cap, and flags that say whether they help or warn,
  with the reason on hover: low float (under 10M shares), SSR, day 2, a reverse split (done, or
  ahead: often an offering follows), dilution risk, earnings today, halts today, watchlist. Then
  the price levels around it, highest first with how far each is: the pre-market high, warrant
  exercise prices and offering prices. And its industry's theme: whom it moves with, or who
  moves with it. Click a ticker for its panel; Back returns to the plan. It refreshes every
  minute while open; after 10:00 it keeps the morning's list.
  - The 09:15 brief carries the plan's main flags too: *CRUX +38.0% · …topline data, low float*.
  - The order is meant to be followed, not a score. It's a starting point for your own checks.
  - Every gapper is measured after the open (see [signals.md](signals.md): Gappers). Once enough
    gappers like a card's were measured (`record_min`, 10), the card says how they did: *gap up ·
    low float: 10:00 +2.10%, close -3.40%, 60% faded (14 in 30 days)*.
- **Price & volume alerts** (bottom-right panel): rules like *AAPL above 350*,
  *all watchlist tickers move ±5%* or *RVOL ≥ 3×*. Leave the ticker blank to apply a rule to
  the whole watchlist. A toggle adds *alert when a stock enters the unusual-volume list*,
  at most once per stock per day, another *alert when a stock trades its whole float* (see
  *Float rotation*), and one for *breakouts* (see below).
  - Rules fire when a level is **crossed**, not while it holds. The first reading after
    startup, or after adding a rule, only records a baseline, so a stock already up 6%
    when the app starts won't alert until it dips below 5% and crosses again. A stock
    hovering around a level alerts at most every 30 minutes.
  - Alerts go to the log in the panel, a desktop notification and a lower-pitched beep than
    news alerts.
  - **Alert kinds** (`app/noise.py`), under the switches: every kind of alert (your rules,
    unusual volume, float rotation, breakouts, sympathy, warrant and offering levels, halts,
    resumptions, LULD) with how many fired today and in the last 7 days, how many went to the
    phone today, and its track record (median move to the close, as in the Reactions tab).
    Untick *Show* to keep a kind off this page, *Phone* to keep it off the phone.
    - *snooze* on any alert in the log: no alerts about that stock, anywhere, for the rest of
      the day (ET). Snoozed stocks are listed here, with × to wake one up early.
    - `phone_cap_per_kind` (Setup → Alert tuning; 0 = no cap): at most that many alerts of one
      kind reach the phone a day; past it they still show here.
    - Muted and snoozed alerts are still counted and measured, so a kind's track record says
      what you'd be missing before you turn it back on. The counts are kept 8 days in
      `data/alert_counts.json`: kinds and numbers only, no tickers.
  - With Finnhub configured, rules are checked on every real-time price update; otherwise on
    each Yahoo refresh (~15 s, prices delayed). Default rules: all tickers ±5%, RVOL ≥ 3×.
    Rules are saved in `data/state.json`.
- **Trading halts** (*Halts* tab, bottom left): every US halt from Nasdaq Trader's free RSS
  feed, polled every 20 s. That covers news halts (T1/T2), regulatory halts (T12, H10…),
  limit-up/limit-down volatility pauses (LUDP) and circuit breakers.
  - Shows halt time, reason (hover for the full description), scheduled resumption and a
    live status: *Halted*, *Resumes in 4:32* (counting down every second, as does the
    **HALTED** badge in the tables) or *Resumed*, with the move from before the halt once known
    (hover for the first trade, the last one before the halt and the first minute's range).
  - *Today* is the default view. *All in feed* adds long-running halts, some of them years old.
  - A red **⏸ N halted** counter in the header shows today's active halts; click it to jump
    to the list. Halted tickers get a **HALTED** badge in every table.
  - Alerts (setting in the Alerts panel): *Watchlist* (default), *All stocks* or *Off*. They
    fire when a halt appears and again when a news halt gets its resumption time. The feed
    contents at startup are the baseline and don't alert. *All stocks* is noisy: a busy day
    has 40+ volatility pauses.
  - **Resumptions** (`app/engine/resumptions.py`), for the same halts: a heads-up about a minute
    before trading resumes (*BOLT resumes trading in about a minute, at 10:05 ET*), and once it
    has, how: *BOLT resumed at 10:05 ET: first trade $4.60, +15.0% from $4.00 before the halt
    (first minute $4.52–$4.71)*. That comes from the stock's 1-minute bars, the first minute
    at or after the resumption against the last trade before the halt (the pause price if the
    chart has none), so it waits for Yahoo's bar, up to 30 minutes. A resumption that happened
    before the app started isn't reported. Both go to the phone with the halts, as high priority.
  - **LULD bands** (`app/luld.py`), estimated: in regular hours a stock may only trade within
    a band around the average price of its last 5 minutes, and at a band it pauses (the LUDP
    halts). The bands are 10% above $3 (5% for Tier 1: S&P 500 and Russell 1000 stocks, guessed
    here as a market cap of $4B or more), 20% from $0.75 to $3, and the lesser of $0.15 or 75%
    below that; doubled from 15:35 for Tier 1 and for Tier 2 at $3 or less (no doubling at the
    open since 2020).
    - A **LULD ↑1.3%** badge marks a stock within a quarter of the band's width of a band; hover
      for the bands. The ticker panel shows the estimated bands all session.
    - For the stocks the halt-alert setting covers, a warning when one nears a band: *BOLT is
      2.2% from its estimated limit-up band of $11.29 (reference $10.26, ±10%): it pauses for 5
      minutes there if it can't get back inside within 15 seconds*. At most every 15 minutes per
      stock and side; to the phone with the halts.
    - An estimate: the reference price is the average of the prices this app saw (every tick
      with Finnhub, a reading a minute or so with Yahoo), not of every trade. Yahoo's prices are
      delayed ~15 minutes, so without Finnhub a warning can arrive after the pause.
    - How good the estimate is gets checked on every LULD pause of a stock the app follows,
      against the official pause price; the Reactions tab shows the result (see
      [signals.md](signals.md): LULD band estimates).
  - Open `http://localhost:8000/#halts` to start on the Halts tab.
- **Sentiment**: every headline is scored −1…+1 by a local finance lexicon (`app/sentiment.py`),
  with no API calls. Items at ±0.35 or beyond get a ▲ bullish / ▼ bearish badge, and hovering
  the badge shows the phrases that drove the score. The watchlist's *News 24h* column is the
  mean score of each ticker's headlines over the last day.
  - Longest phrase wins: "public offering" −2 beats "offering" −1, and "initial public
    offering" is neutral. A negator up to 3 words before a phrase flips it, so "did not meet
    its primary endpoint" is bearish.
  - It's a word-matching model, so it misses sarcasm and context ("growth" in a product
    name still counts as bullish). To tune it, edit `LEXICON_SOURCE` and add a case to
    `tests/test_sentiment.py`.

## Live updates

The page gets what changes, as it changes, over one connection: the quotes after each refresh
(and with Finnhub only the stocks whose price moved, up to twice a second), the movers, the
gappers when the list changes, sympathy moves when who moves with whom changes, and only the
stocks near a LULD band (the ticker panel asks for its own bands). On a busy made-up hour (30
watchlist stocks, 100 movers, 80 pre-market candidates) that is about 1.35 MB an hour to each
open page, and the engine's own work takes a few milliseconds a minute; the rest is waiting on
Yahoo and SEC.
