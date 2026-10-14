# Using Market Pulse: a trading day

How the pieces fit into a day, in the order you'd use them. Every feature has its full
description in [dashboard.md](dashboard.md) and [signals.md](signals.md). Back to the
[README](../README.md).

Market Pulse points at things to look at; it doesn't tell you what to trade. Without a Finnhub
key its prices are about 15 minutes delayed.

## Before the open (04:00–09:30 ET)

1. **Plan** (header button). Today's gappers, catalyst first, each in one card:
   - the gap, the price and how long it has traded pre-market;
   - the catalyst: a headline or an SEC filing since yesterday's close;
   - flags in green (a tailwind: low float, SSR on a gap up) and red (a warning: dilution risk,
     a reverse split ahead, halted). Hover any flag for why;
   - the price levels around it, highest first, with how far each is: the pre-market high,
     warrant exercise prices and recent offering prices;
   - its industry: who it moves with, or who moves with it;
   - once enough history exists, how gappers like it did: *gap up · low float: 10:00 +2.1%,
     close -3.4%, 60% faded*.
2. **09:15 brief** on your phone: the biggest gappers with their catalyst and main flags, and the
   watchlist's earnings and reverse splits today.
3. Click a ticker for its **panel**: chart (with your alert levels, the filings' levels and the
   pre-market high), stats, dilution profile with every reason, the industry's movers today,
   news and halts. **Click the chart** at a price to set an alert there.

## At the open (09:30–10:00)

- **Breakout alerts**: a gapper going through its pre-market high; later, a new high of day after
  a real pullback.
- **Halts**: a *HALTED 4:32* badge counts down to the resumption; you get a heads-up a minute
  before and the first trade after (*resumed at 10:05 ET: first trade $4.60, +15.0% from before
  the halt*).
- **LULD** badges and warnings when a stock nears its estimated limit-up or limit-down band,
  where a 5-minute pause comes.
- **SSR**, **ROT** (traded its whole float) and **SYM** (moving with its industry's leader)
  badges appear as they happen.

## During the day

- **Watchlist rules**: *above / below* a price, *moves ±%*, *RVOL*. Fastest is clicking the chart.
- **News feed**: filter by ticker or text, source, sentiment, watchlist only, alerts only. SEC
  filings are in it with plain-English labels (*offering from shelf*, *unregistered share sale*),
  the offering price and warrant terms, insider buys, 13D stakes and 8-K events.
- **Unusual volume, Halts, Gaps** tabs (bottom left) and **Top movers** (top right).
- **Too many alerts?** Alerts panel → *Alert kinds*: each kind with today's and this week's
  count and its track record; untick *Show* or *Phone* to mute one. Or *snooze* a stock from any
  alert for the rest of the day.

## After the close

- **Daily recap** on your phone at 16:30 ET: the watchlist's moves, halts, filings, the biggest
  reactions, and a line on your alerts.
- **Reactions** tab: what actually happened after each kind of event, over 7/30/90 days.
  - *By catalyst*: filings, headlines and halts.
  - *Your alerts*: did breakouts, level crossings and rotations follow through, or fade?
  - *Gappers*: which plan flags worked: low float, SSR, day 2, with a filing...
  - *Offerings by dilution risk*: does *high* risk really fall harder?
  - *LULD band estimates*: how close the estimates came to the official pause prices.
  - *Download CSV* for your own spreadsheet.

## Every week or so

- Look at the track records and tune Setup → **Signal thresholds** (the pullback before a new
  high counts, day 2, float rotation, LULD nearness, sympathy, how much history before a track
  record is quoted). Mute alert kinds that don't follow through.
- Setup → *Your data* → **Download backup** (your watchlist, rules, switches and settings; never
  a secret).

## The badges

| Badge | Means |
|---|---|
| HALTED 4:32 | Trading halted; counting down to the resumption |
| DIL | Dilution risk high (red) or medium (amber), from SEC filings; hover for why |
| WAR | The price is above a warrant exercise price: those warrants can be exercised at a profit |
| SSR | Short sale restriction today (Rule 201) |
| ROT 1.3× | Today's volume is 1.3× the stock's whole float |
| RS 1:20 | A reverse split taking effect soon, or just done (outlined) |
| D2 | Day 2: up 30%+ in the last session |
| SYM | Moving with the leader of its industry today |
| LULD ↑1.3% | 1.3% from its estimated limit-up (↑) or limit-down (↓) band |
| ER | Reports earnings within 5 days (filled: today) |

## Handy

- `http://localhost:8000/#ticker=ACME` opens a ticker, `#plan` the plan, `#gaps` / `#halts` /
  `#reactions` a tab.
- Esc or the phone's Back gesture closes the panel, the plan and Setup.
- On a phone the dashboard is one column; add it to the home screen to open it like an app.
