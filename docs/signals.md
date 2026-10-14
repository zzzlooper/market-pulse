# Signals, filings and tracking

SEC filings and dilution, the short sale restriction, float rotation, reverse splits,
day-2 runners, sympathy movers, breakouts, earnings, pre-market gaps, catalyst reactions and
data health. Back to the [README](../README.md).

- **SEC filings** (`app/filings.py`): filings that move small caps appear in the news feed
  with a coloured badge, the form type and a one-line note on what the filing means.
  - *Dilution* (red): offering registrations (S-1, F-1), shelf registrations (S-3, F-3),
    priced offerings and shelf takedowns (424B1/B4/B5) and resale prospectuses (424B3/B7).
    Only listed companies are shown. 424B2 is skipped: banks file thousands a day for
    structured notes.
  - *Insider buy* (green): open-market purchases on Form 4 worth at least `insider_min_value`
    (default $50K), from any company, with the insider's role, size, average price and how much
    the holding grew. Trades under a 10b5-1 plan are marked. *Insider sales* show only for
    watchlist stocks. Awards, option exercises and tax withholding are ignored.
  - *13D stake* (blue): an investor crossing 5% with an active intent, with the stake size.
  - **Warrants**: for offerings and private share sales (8-K 3.02), the first pages of the
    filing are read for warrants (`app/warrants.py`). If the offering sells them, the alert and
    news item say *· with warrants* and give what the cover states, e.g. *pre-funded warrants for
    400K shares, warrants for 43.6M shares (Series D) at $0.66*. Warrants already outstanding go
    into the item's description. Shelf boilerplate ("we may offer… warrants") doesn't count. Best
    effort: wording varies, so a miss means "not on the first pages", not "none". Only the
    numbers are kept; no text from the filing, which can name people.
  - *8-K events* (amber): every 8-K is labelled by its items (earnings, material agreement…).
    The notable ones get an amber badge and a note on what they mean: delisting notice (3.01),
    unregistered share sale (3.02, dilution that never shows up as a prospectus),
    shareholder-rights change (3.03, usually a reverse split), auditor change (4.01),
    financials unreliable (4.02), bankruptcy (1.03), debt acceleration (2.04), change in
    control (5.01) and cybersecurity incident (1.05). Any watchlist 8-K alerts; for movers
    only the notable ones do.
  - *Reverse splits* (`app/splits.py`): an 8-K with item 3.03 or 5.03 has its first pages read
    for a reverse split's ratio and the day it takes effect, and its alert and news item say
    so: *Bolt Battery Inc: shareholder rights change · 1-for-20 reverse split*, *1-for-20
    reverse split, effective Oct 15, 2026*. Ratios are read as "1-for-20", "one-for-twenty
    (1:20)", "1:20" or "20-for-1"; a range a shareholder vote allows ("within the range of
    1-for-5 to 1-for-30") and the many 8-Ks that only mention splits in passing (anti-dilution
    clauses) don't count. Only the ratio and the date are kept, no text.
  - Before 10:00 ET a stock in the Gaps list counts as moving too, e.g. *ACME · offering from
    shelf (gapping -22% pre-market, dilution risk HIGH)*.
  - A filing alerts (log, desktop, phone) when the stock is on the watchlist **or on today's
    movers or unusual-volume lists**. An offering right after a run-up is the classic case,
    and the phone message says why, with the dilution risk when it's high or medium, e.g.
    *ACME · offering from shelf (top gainer +45%, dilution risk HIGH)*.
    Filings already in the feeds at startup don't alert.
  - Privacy: Form 4 and 13D filings name insiders and individual investors, often with their
    addresses. Only roles (CEO, Director), share counts, prices and stake sizes are read. A 13D
    filer is named only when it is clearly a firm (LLC, LP, Ltd, Fund, Capital…), never a
    person. Your `sec_user_agent` is sent only to SEC hosts (www.sec.gov, data.sec.gov), by one
    client that refuses redirects, and is never logged. The dilution panel passes on only the
    figures it computes, none of the addresses or phone numbers in SEC's responses.
  - `sec_filings = false` turns it off (8-Ks then show as plain news, as in v0.1). EDGAR doesn't accept filings on weekends and federal
    holidays, and insiders have two business days to file a Form 4.
- **Short sale restriction** (`app/ssr.py`): an **SSR** badge, in every table and the panel's
  price line, when SEC Rule 201 is in effect: the stock traded 10% or more below the previous
  close during regular hours, today (so it holds tomorrow too) or in the last session (so it
  holds all of today). Shorts then can't sell at or below the bid, which takes pressure off the
  price and makes squeezes likelier. Hover for which. The last sessions' daily bars are looked
  up once a day for the watchlist, the movers and the gappers, so a pre-market gapper shows a
  restriction carried over from yesterday. It's worked out from prices (the rule itself goes by
  the best bid), so treat it as a strong hint; your broker's flag is the final word.
- **Float rotation**: a **ROT 2.3×** badge in the watchlist, movers and unusual-volume tables
  once today's volume has reached the stock's whole public float (Yahoo's figure), and the
  ticker panel's *Float* shows the multiple. Low-float runners that rotate their float are a
  classic momentum setup. An alert fires the first time a watchlist stock or mover crosses 1×
  in a day (the first reading a baseline), unless switched off in the alerts panel; on the phone
  it comes under *Unusual volume*, off by default. Floats are looked up once a day per stock,
  20 at a time; before the open the volume shown is yesterday's, so nothing counts until today's
  session starts.
- **Reverse splits**: an **RS 1:20** badge, in every table and the panel's price line, on a
  stock whose reverse split takes effect within 30 days or did in the last 5 (outlined once
  it's done); hover for the ratio, the date and the 8-K. Every 20 shares become one, so the
  float shrinks 20×: freshly split stocks often run, and offerings often follow. The dilution
  profile reads the 2 newest 8-Ks with item 3.03 or 5.03 from the last 60 days (watchlist,
  gainers and small caps, like the DIL badge), and a split 8-K arriving in the feed badges the
  stock at once. The panel's dilution figures show it too, and the 09:15 brief ends with
  *Reverse splits today: BOLT 1-for-20*.
- **Day-2 runners**: a **D2** badge on a stock that closed 30% or more higher in the last
  session than the one before: its second day, when continuation (and the fade) are classic
  setups. From the daily bars already read for SSR, for the watchlist, the movers and the
  gappers; before the open the movers lists still hold yesterday's session, so yesterday's
  runners are covered. The brief marks day-2 gappers: *ACME +22.0% · day 2 (+45% yesterday)*.
- **Sympathy movers**: in each industry, the biggest gainer up 30% or more today leads it, and
  same-industry stocks up 5% or more get a **SYM** badge (hover for the leader), among the
  stocks the app follows (watchlist, movers, gappers; industries from Yahoo's company data, read
  once a day with the float). The first time a stock moves in sympathy in a day it alerts
  (*DYNA +12.0% in sympathy with CRUX +41% (Biotechnology)*, with the breakouts: same switch,
  same phone category), and a breakout alert on it names the leader too.
  - The ticker panel's **Same industry today** lists the industry's biggest gainers across
    the whole US market from Yahoo's screen (Nasdaq and NYSE listed, $1 or more; no OTC),
    refreshed every 2 minutes while open: the theme in play, and who's next.
- **Breakouts** (`app/breakouts.py`): alerts at the moments momentum traders act, for
  watchlist stocks, gappers and movers, after the open:
  - **through the pre-market high**: the highest pre-market trade, from the gap scanner's
    1-minute bars (it covers the watchlist, stocks with fresh news or filings, and the last
    session's movers). The last pre-market price is the baseline, so a stock that opens
    straight through its pre-market high counts. Once a day per stock.
  - **a new high of day after a pullback**: a new regular-session high after the price fell at
    least 5% below the previous one, at most every 15 minutes per stock. Without the pullback, a
    runner would alert on every tick up.
  - Switch them off in the alerts panel. On the phone they're a category of their own,
    *Breakouts*, off by default since busy days bring many; switched on, they're high priority.
    Prices arrive at the app's polling rate (a minute or so with Yahoo, seconds with Finnhub),
    and Yahoo's are delayed ~15 minutes, so these are as timely as the price source.
- **Earnings** (`app/earnings.py`): the next report date of each watchlist stock, looked up
  every 6 hours and when the watchlist changes. An **ER** badge marks reports within 5 days
  (filled on the day; hover for when). The 09:15 brief lists the watchlist's reports today, and
  the recap tonight's and the next trading day's. Yahoo's dates are reliable but its times often
  aren't (a 15:00 placeholder is common), so a time is only shown when it's clearly before the
  open or after the close; otherwise the time isn't confirmed. Estimated dates are marked.
- **Pre-market gaps** (*Gaps* tab, bottom left; `app/gaps.py`): from 04:00 to 10:00 ET on
  weekdays, every minute, the stocks opening well away from yesterday's close (default 3%,
  `gap_min_pct`), biggest first, with why each was scanned: the filing or headline behind it,
  the watchlist, or yesterday's movers. DIL and HALTED badges as everywhere.
  - Yahoo has no pre-market screener, so the scan covers the watchlist, every stock with a
    filing or headline in the last 18 hours, and the last session's movers (at most
    `gap_max_symbols` = 80), in one batched download a minute. A new filing, headline or
    movers list rescans at once.
  - Yahoo's pre-market bars have prices but no volume, so activity is counted in minutes with
    a trade (*Active*); a gap needs `gap_min_minutes` (5) of them, so one stale print doesn't
    count. After 09:30 the gap is measured at the open.
  - A **pre-market brief** goes to the phone at `gap_brief_time` (09:15 ET): the biggest gaps
    with their cause, whether it's day 2 and any high or medium dilution risk, and the reverse
    splits taking effect that day. Skipped on mornings without
    pre-market trading. Open `http://localhost:8000/#gaps` to start on the tab.
- **Catalyst reactions** (*Reactions* tab, bottom left; `app/reactions.py`): every filing
  (routine 8-Ks only for watched and moving stocks), new trading halt and alerting headline
  is recorded with the price when it arrived, then 5 and 30 minutes later and at the close
  of the next regular session.
  - **Your alerts: what happened next**: the app's own alerts are measured the same way, from
    the price when they fired: breakouts (pre-market high, new high after a pullback), warrant
    and offering-price crossings, float rotations, unusual-volume entries, halt resumptions and
    your own price/volume rules. Grouped by alert, it shows which ones the stock followed
    through on and which were noise, e.g. whether pre-market-high breaks held to the close.
    They're kept apart from the catalysts, and the recap gets one line for them.
  - **Track records in the alerts**: once 10 alerts of a kind have been measured to the close
    in the last 30 days, each new one says how that kind has done, e.g. *ACME broke its
    pre-market high of $5.00 (now $5.10) · track record: 30 min +2.0%, close -4.0%, 58% closed
    lower (median of 12 in 30 days)*, on screen and on the phone. Until then they say nothing.
  - The tab shows the median move per catalyst type over 7/30/90 days, with *n* (tracked ·
    measured to the close) and the share that closed lower, and the same for offerings
    grouped by the stock's dilution risk. That's the check on the risk score: if *high* doesn't
    fall harder than *low* after a few weeks, the weights need work. Every offering's risk is
    looked up as it's tracked, so the level already counts that filing.
  - Prices are Yahoo's intraday bars, read once they've arrived (20 minutes after each
    horizon): 1-minute bars with pre/post-market for today, 5-minute bars back to 5 days.
    An event overnight has no 5m/30m reading; it's measured from the last trade before it to
    the next close. Bars come one chart per stock per round, at most 20 a round.
  - Stored in `data/reactions.db` (SQLite, readable by you only): tickers, event types,
    headlines, times and prices, and the alerts that fired for you. Those include your own
    price rules, so the file says which stocks you watch and at what levels; it never leaves
    your machine, and *Setup → Your data* can delete it.
  - *Download CSV* saves the tracked catalysts and alerts and their moves for the chosen period, for your
    own spreadsheet: time, ticker, catalyst, context, dilution risk, the 5m/30m/close moves and
    the headline. Headlines come from outside feeds, so any starting with = + - @ get a leading
    apostrophe and open as text, never as a formula.
  - *n* is small for weeks; a median of 3 says little.
- **Data health**: the header shows whether every data source is delivering (Yahoo prices,
  Yahoo movers, news feeds, Nasdaq halts, SEC filings, and the Finnhub stream when configured). Click it
  for each source's last update and error.
  - A source is judged by the age of its last good update, not just by errors: a yfinance
    call that hangs never raises an error, so no data for 3 polling intervals (at least 60 s)
    counts as **down**. *Partial* means some data is missing, e.g. one screener failed or a
    ticker got no price.
  - A panel fed by a source that's down gets a red *stale · 4m ago* badge and is dimmed, so
    old numbers don't pass for live ones. When Yahoo fails, the watchlist keeps the last
    prices instead of going blank; a ticker missing from an otherwise good update is dimmed.
  - A news feed is only reported after failing 3 polls in a row: single misses are common
    (PR Newswire sometimes redirects to a URL that 404s for one request).
- The watchlist, keywords and alert settings are saved to `data/state.json`.

