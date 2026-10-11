# Pure Technical Long/Short Screener

A cross-sectional, pure-technical stock screener over **S&P 500 + S&P 400 +
S&P 600 + Russell 2000** (~2,865 unique tickers), with an interactive dashboard
for ranking longs and shorts, filtering on price and liquidity, and filtering
on optional setup flags.

Everything the model uses comes from **daily OHLCV price history only** — no
fundamentals, no estimates, no news.

---

## Quick start

```bash
# 1. (first time only) install what's missing
pip install yfinance pandas numpy pyarrow lxml streamlit
pip install --target ./vendor plotly     # optional, nicer charts

# 2. build the screen
python run_dashboard.py --run --mode pipeline

# 3. explore the saved CSV — filtering, sorting, charts
python run_dashboard.py
```

Then open the URL it prints. There are **two front ends**:

| Command | Serves | What it's for |
|---|---|---|
| `python run_dashboard.py` | [`dashboard.py`](dashboard.py) | **CSV explorer** — filter, sort and chart the saved screen. No pipeline, nothing downloaded. |
| `python run_dashboard.py --mode pipeline` | [`app.py`](app.py) | **Pipeline dashboard** — runs/refreshes the screen and shows it, with custom factor weights and candlestick drill-down. |
| `python run_dashboard.py --run` | both (runs first) | Build/refresh the screen, then serve the pipeline dashboard. |

### Other entry points

```bash
python universe.py            # build/refresh only the four constituent lists
python data.py                # build/refresh only the price cache
python screener.py            # run the screen, print top 20 longs and shorts
python smoke_test.py          # validate the maths on a 22-ticker basket (~40s)
python test_app.py            # execute the pipeline dashboard headlessly
python test_dashboard.py      # execute the CSV explorer headlessly
python verify.py              # end-to-end check of all five deliverables
```

`run_dashboard.py --run --force` forces a full re-download of both constituents
and prices.

---

## What it does

```
 ┌──────────────┐   ┌────────────┐   ┌──────────────┐   ┌─────────────────┐
 │  universe    │ → │  prices    │ → │  features    │ → │  score & rank   │
 │ 4 index      │   │ yfinance   │   │ ~89 technical│   │ long / short    │
 │ constituent  │   │ 2y daily   │   │ indicators   │   │ + flags + levels│
 │ lists        │   │ OHLCV      │   │ (wide frames)│   │                 │
 └──────────────┘   └────────────┘   └──────────────┘   └─────────────────┘
```

| Stage | Module | Notes |
|---|---|---|
| Universe | `universe.py` | S&P 500/400/600 from Wikipedia tables, Russell 2000 from the iShares IWM full-holdings workbook. Each has the other as a fallback, plus a disk cache, so it survives either source changing. |
| Prices | `data.py` | Batched, retried, **checkpointed per batch** and resumable. Incremental: a re-run fetches only what's missing. Total-return adjusted. |
| Indicators | `indicators.py` | ~30 vectorised indicators over wide (date × ticker) frames — SMA/EMA/RMA, Wilder ATR/RSI/ADX, MACD, Bollinger, Stochastic, Williams %R, ROC, realised vol, OBV, A/D, CMF, MFI, VWAP, Donchian, rolling percentiles. |
| Scoring | `factors.py` | Six factor groups → Gaussian-rank normalisation → weighted composite → absolute risk penalties → long/short scores, ranks, flags, ATR trade levels. |
| Orchestration | `screener.py` | Runs all stages, saves `output/latest_screen.csv` + a dated copy + run metadata. |
| Dashboard | `app.py` | Pipeline UI: ranking, price/liquidity/risk filters, optional-flag filters, custom factor weights, charts, ticker drill-down, research prompts. |
| CSV explorer | `dashboard.py` | Standalone explorer over `output/*.csv`: filter (book, optionable, price, index, sector, liquidity, risk, flags, search), explicit multi-key sorting, 5 tabs of charts and drill-down. |
| Research prompts | `research_prompt.py` | Turns any screened row into a copy-paste prompt for ChatGPT / Claude / Gemini / Perplexity: **what was the fundamental story behind this move, and is it likely to continue over the next 1–3 months**. The widgets live in `prompt_ui.py` and are shared by both front ends. |

Full reasoning behind every factor choice: **[TECHNICAL_JUSTIFICATION.md](TECHNICAL_JUSTIFICATION.md)**.

---

## The dashboard

**Sidebar**

| Control | Purpose |
|---|---|
| ▶ Run screen / ↻ Refresh data | Rebuild the screen in place, with a live progress bar |
| Indices included | Restrict to any subset of the four universes |
| Show: Longs / Shorts / Both | Long book, short book, or the top of each side by side |
| **Price range** | Dollar price band — the price filter |
| Min 20-day $ volume | Liquidity floor |
| Max ATR% | Cap how much daily range you will hold |
| ADX range / RSI range | Trend-strength and overbought/oversold bands |
| **Must have / must not have flags** | The **optional flag filter** — pick any subset of 19 boolean setup flags, AND or OR, and exclude ones you don't want |
| Optionable only | Price/liquidity proxy for listed options (and for shortability) |
| **Rank by** | 13 preset ranking metrics per side, or **your own factor weights** |
| Rows to show | Table depth |

**Tabs** — `Screener` (the ranked table, with a **📋 Copy button on every row**
and CSV export), `Research` (the copy-paste prompt for any name in the current
view, plus a batch CSV — see
[Research prompts](#research-prompts--the-hand-off-to-a-chatbot) below),
`Charts` (score distribution, sector mix, momentum-vs-risk bubble chart, factor
contribution profile), `Ticker detail` (candlestick with SMA20/50/200 and volume,
the exact trade levels the model computes, active flags, factor breakdown, the
research prompt), `Methodology` (in-app explanation).

---

## The CSV explorer (`dashboard.py`)

Point it at any CSV in `output/` (or upload your own) and it does nothing but
filter, sort and chart — no pipeline, no network calls.

**The three headline filters come first in the sidebar:**

| # | Filter | Behaviour |
|---|---|---|
| 1 | **Book** | `Longs` / `Shorts` / `Both`, plus a rank cutoff for the top N of that book and a composite-score floor. Longs and Shorts use the screener's own classification — a name is a long if its long composite beats its short composite — so they partition the universe (1,153 + 1,113 = 2,266 in the current run). |
| 2 | **Optionable** | `All` / `Optionable only` / `Non-optionable only` — a clean partition (2,093 + 173 = 2,266). |
| 3 | **Price** | Dollar range slider plus quick bands (≤$25, $5–100, $25–500, ≥$200). |

Then: index membership (multi-select — a ticker can belong to several), sector,
liquidity floor, ATR% cap, ADX floor, RSI band, 52-week range position,
**must-have / must-not-have optional flags** (AND or OR), and free-text search
across ticker, company name and sector.

**Sorting** is set in the sidebar — a sort column, a direction toggle, and an
optional secondary column, any of the 134 numeric fields — and it decides the
page order. Clicking a column header also re-sorts *the page you are looking
at*, which is handy for eyeballing a shortlist without touching the filters.

### Column visibility — shrinking the table

Pick one of 10 column-set presets, then edit the selection freely: the
**🧱 Columns** panel in the sidebar lists every column and lets you remove any
of them to narrow the table, or add back ones you want. The count of selected
columns is shown underneath, and the header reports the resulting width.

Six shortcut buttons sit below the picker:

| Button | Effect |
|---|---|
| `All` | show every column |
| `Preset` | back to the selected column set |
| `None` | clear the selection, then add only what you need |
| `− flags` | drop the 19 `flag_*` columns |
| `− factors` | drop the `f_*` factor-leg columns |
| `− levels` | drop raw indicator levels (SMA/EMA/ATR, stop/target/trail, entry, 52-week high/low) |

Display order is fixed regardless of the order you click, so the table stays
stable as you toggle. Selecting nothing warns instead of rendering an empty grid.

### Pagination

The `Table` tab has its own paging bar: **rows per page** (10 / 25 / 50 / 100 /
200 / 500), first / previous / next / last buttons, and a **Go to page** box.
The header and the bar both report `page X of Y` and the exact row range.
Changing the page size, a filter, or the sort order jumps back to page 1, and
the page number is clamped if a filter shrinks the result set beneath it.
The download button always exports the **whole filtered set**, not just the
visible page.

**Tabs**

* `Table` — the paged, column-configurable grid, with CSV export of exactly
  what you filtered. The first column is a **📋 Copy button** — pinned to the left
  so it stays put while you scroll right — and clicking it puts that ticker's
  whole research prompt on the clipboard. The row reads `📋 | # | Score | Ticker`,
  i.e. the ranking metric sits right next to the name. Headers are click-to-sort
  (the current page), and the sidebar's column picker drives what the table shows.
* `Research` — pick any name in the filtered set and copy its prompt; also
  builds a batch CSV of prompts for the top N of the current view (see
  [Research prompts](#research-prompts--the-hand-off-to-a-chatbot)).
* `Charts` — score distribution by side, sector counts, a **build-your-own
  scatter** (any X, any Y, colour by any field, bubble size by liquidity/ATR/flags),
  ATR%-vs-6m-return, log price distribution, a factor-profile heatmap of the top 20,
  and a top-30 bar chart. Charts use the whole filtered set, not the current page.
* `Long vs Short` — side-by-side medians (price, ATR%, liquidity, RSI, ADX,
  returns, volatility, optionable share), mean factor profile per book, and flag
  prevalence per book.
* `Ticker detail` — every field for one name, its model trade levels, active
  flags, its factor scores as a bar chart, and the research prompt.

Nine one-click **presets** (momentum longs, pullback longs, breakouts, squeeze
watchlist, volume spikes, downtrend shorts, capitulation shorts, fresh crosses,
liquid optionable large caps) plus a **Reset all** button.

### Research prompts — the hand-off to a chatbot

The screen is **pure technical by construction**: it can tell you what the tape
is doing, never *why*. So every ticker comes with a ready-to-paste prompt that
asks a chatbot the two questions the model cannot answer:

1. What was the **fundamental story** behind this share price move?
2. Is it **likely to continue over the next 1–3 months**, and why?

Ways to grab one:

| Where | How |
|---|---|
| **`Table` / `Screener` tab — 📋 button on every row** | One click copies that ticker's whole prompt. This is the fast path: scan the grid, copy the names you care about. |
| `Research` tab | Pick any name in the filtered set from a dropdown, and read the prompt in full. |
| `Ticker detail` tab | Sits under the candlestick and the model's trade levels. |

The grid *is* the copy table: a plain HTML table (a grid cell cannot hold a
button), so the row you copy from is the row you were reading. It is capped at
100 rows — the visible page — and inherits the app's theme. The button flashes
**✓ Copied**, and falls back to a select-and-copy box if the browser blocks
clipboard access.

The block is rendered as a code box, so the **⧉ copy icon** at its top-right puts
the whole prompt on the clipboard; **⬇ Download .txt** keeps a copy, and the
`Research` tab also builds a **batch CSV** — one row per ticker, prompt in the
last column — for the top N of the current view.

The sidebar (`💬 Research prompts`) switches between two styles:

| Style | What it is |
|---|---|
| **Full research brief** | ~10,500 characters: the whole technical snapshot, then the fundamental story, the KPI trend versus peers, what is priced in, a committed call (direction + probability + price target + 1–3 reasons), dated catalysts, bull/base/bear cases, invalidation criteria and a monitoring list. |
| **Short prompt** | ~1,300 characters: the same three questions with only the headline numbers, for a quick chat. |

Two parts of the brief are worth knowing about, because they are what turn it
from a research request into a decision:

* **The call.** The model must open with one line in a fixed shape —
  `CALL: HIGHER|LOWER — n% — target $price (±x% from today) by date` — then give
  1–3 plain reasons (never more than three, so the thesis stays simple). Hedging,
  ranges-instead-of-a-call and both-cases answers are ruled out, and the call has
  to be the highest-probability of its own bull/base/bear scenarios.
* **The KPI trend.** It must pull the KPIs that actually drive the business —
  revenue, margin, EPS, cash flow plus the industry-specific ones — quarterly for
  the last 8–12 quarters, show them as a chartable table (or an actual chart if it
  can render one), and do the same for the 2–4 closest peers over the same
  quarters, so the **levels and the rates of change** can be compared and set
  against the share price move.

Both carry the row's own numbers, so the chatbot needs no other context: price
and daily change, 52-week high/low and range position, 1/3/6/12-month and 12-1
returns, excess return versus the universe, RSI(14)/RSI(2), Stochastic, Williams
%R, Bollinger %B and bandwidth percentile, ADX with ±DI, the three SMAs and the
distance to each, realised volatility, relative volume, Chaikin money flow, MFI,
OBV slope, VWAP distance, ATR and ATR%, the model's entry/stop/target/trail and
R-multiple, the active setup flags, and the six factor legs.

The prompts also tell the model to **say whether it can browse**, to cite sources
with dates, to answer in a fixed shape (the one-line call → bottom line → story →
KPI trend vs peers → what is priced in → the next 1–3 months → what would prove it
wrong → a monitoring list), and to skip generic investing boilerplate. `research_prompt.py` builds the text and has no
Streamlit dependency; `prompt_ui.py` renders it for both front ends.

### Optional flags

These are computed as booleans for every name and are filterable in any
combination:

`uptrend`, `downtrend`, `above_200sma`, `golden_cross`, `death_cross`,
`macd_bull_cross`, `macd_bear_cross`, `rsi_oversold`, `rsi_overbought`,
`rsi2_extreme`, `bb_squeeze`, `volume_spike`, `near_52w_high`, `near_52w_low`,
`new_high_252`, `new_low_252`, `breakout_20d`, `breakdown_20d`, `gap_up_today`

plus `is_optionable`.

---

## Output columns

The screen exports ~120 columns. The ones you will actually use:

**Identity** `ticker`, `name`, `sector`, `universe`, `universes`, `side`

**Ranking** `long_score`, `short_score`, `long_pct`, `short_pct`, `rank_long`,
`rank_short`, `long_score_adj`, `short_score_adj` (regime-tilted)

**Factor legs** `f_trend_long`, `f_momentum_long`, `f_riskadj_long`,
`f_relstrength_long`, `f_volume_long`, `f_entry_long` — and the `_short`
equivalents. All in cross-sectional σ units.

**Price & trend** `close`, `chg_1d_pct`, `sma20/50/200`, `dist_sma20/50/200_pct`,
`above_sma20/50/200`, `adx14`, `plus_di`, `minus_di`

**Momentum** `ret_1m`, `ret_3m`, `ret_6m`, `ret_12m`, `ret_12_1`, `rs_ret_*`,
`up_day_ratio_63`, `mom6_over_vol`, `vol_21`, `vol_63`

**Timing** `rsi14`, `rsi2`, `stoch_k`, `williams_r`, `pct_b`, `zscore_20`,
`stretch_atr_20/50/200`, `range_pos_52w`, `pct_to_52w_high`, `pct_above_52w_low`

**Flow** `rvol`, `obv_slope_63`, `cmf_21`, `mfi14`, `vwap20_dist_pct`,
`avg_dollar_vol_20`, `avg_dollar_vol_m`

**Risk & execution** `atr14`, `atr_pct`, `entry`, `stop_long`, `target_long`,
`trail_long`, `stop_short`, `target_short`, `trail_short`, `risk_pct`,
`shares_for_1pct_risk`

**Flags** `flag_*` (booleans), `flags` (comma-joined string), `n_flags`,
`eligible`, `is_optionable`

---

## Configuration

Everything tunable is in [`config.py`](config.py):

* universe cache TTL, price period, batch size, retries
* eligibility gates (`MIN_PRICE`, `MIN_DOLLAR_VOLUME`, `MIN_BARS_REQUIRED`)
* all indicator periods
* `LONG_WEIGHTS` / `SHORT_WEIGHTS` — the factor weights
* over-extension and pullback parameters
* regime tilt multipliers
* ATR stop/target/trail multiples and account risk

The dashboard also lets you override the factor weights live, without editing
the file.

---

## Performance

On a cold cache the full pipeline (2,865 tickers, 2 years of daily bars):

| Stage | Time |
|---|---|
| Universe build | ~10 s (or instant from cache) |
| Price download | ~6 min (32 batches, checkpointed) |
| Feature computation | ~10 s |
| Scoring | ~5 s |
| **Warm re-run** (cache hit) | **~20 s** |

Caches live in `cache/` and results in `output/`. To force a complete rebuild,
delete those two directories.

---

## Caveats

* `yfinance` is an unofficial, rate-limited interface. Occasional tickers fail
  and are excluded by the history gate — the run log reports how many.
* `is_optionable` is a **price/liquidity proxy**, not an options-chain lookup.
  A true test would need ~2,900 chain requests per run.
* The universe is *today's* index membership. Fine for live screening; a
  historical backtest would need point-in-time membership to avoid
  survivorship bias.
* No historical validation of this exact parameterisation has been run — see
  §9 of the justification for what that test looks like and why it matters.

---

## Requirements

Python 3.8+, `yfinance`, `pandas`, `numpy`, `pyarrow`, `lxml`, `streamlit`.
`plotly` is optional (vendored into `./vendor` if installed with `--target`);
the dashboard degrades gracefully to Streamlit's native charts without it.
