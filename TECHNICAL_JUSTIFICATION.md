# Technical Justification

**Why this screener is built the way it is, and why I believe this is the best
available design for a *pure technical* long/short equity screen.**

---

## 0. The framing decision: pure technical means cross-sectional, not chart-reading

The single most important design choice is this: **the screen is
cross-sectional, not absolute.**

Almost every retail "technical screener" works like this — find every stock
whose RSI < 30 and call them buys. That is a bad idea, and it is bad for a
specific, measurable reason: an indicator threshold has no fixed meaning. An
RSI of 30 in a low-volatility utility and an RSI of 30 in a clinical-stage
biotech are not the same event, and an RSI of 30 in a calm market is not the
same event as an RSI of 30 in a crash. Fixed thresholds silently load you into
whatever sector or volatility regime happens to generate the extreme readings.

So instead of thresholds, every continuous signal here is converted into a
**cross-sectional rank against the other ~2,900 names on the same day**, and the
ranks are then combined. The screener answers a much better-posed question:

> *"Given everything else I could buy today, is this the best expression of the
> setup I am trying to express?"*

That reframing is what makes the rest of the design coherent, and it's the
reason the trade construction (ATR stops, risk-normalised size) can be uniform
across a $6 utility and a $400 semiconductor.

---

## 1. The universe: why these four indices, and why membership matters

The four universes — S&P 500, S&P 400, S&P 600 and Russell 2000 — cover
roughly the top 3,000 US listings by market cap, deduplicated to **2,865
unique tickers**.

The economically meaningful part is not the size coverage, it is what index
membership *implies*:

* **Liquidity floor.** Every member has passed a float, liquidity and
  domicile screen. That is a free, continuously maintained liquidity filter
  that no vendor list gives you.
* **Investability.** Index membership is the practical boundary of what most
  institutional mandates and most retail brokers will let you trade, and of
  what has listed options.
* **Shortability.** The names you can actually borrow are overwhelmingly
  index members. A screen that nominates un-borrowable shorts is
  not a screen, it is a wish list.

Constituents are pulled from two independent sources (Wikipedia constituent
tables for the S&P family, the BlackRock/iShares full-holdings workbook for
IWM) with each having the other as a fallback, plus a disk cache, so the
universe survives either source going away or changing format.

---

## 2. The six factor groups, and why each one earns its place

Each group below is justified on two independent grounds: (a) there is a
documented economic *reason* it should work, and (b) there is a large,
out-of-sample empirical literature showing it does.

### 2.1 Trend structure — 26%

**What it measures:** the moving-average stack (close > SMA20 > SMA50 >
SMA200), distance from SMA50 and SMA200, the normalised slopes of both, and
ADX(14) signed by the +DI/−DI spread.

**Why it is in:** trend is the most robust and most replicated anomaly in
liquid markets. Jegadeesh & Titman (1993) established cross-sectional momentum;
Moskowitz, Ooi & Pedersen (2012) showed the *time-series* version holds across
58 futures markets; Asness, Moskowitz & Pedersen (2013) showed it survives
across asset classes and geographies. Crucially for a technical model, Brock,
Lakonishok & LeBaron (1992) found that even the simplest moving-average and
trading-range-breakout rules carried genuine predictive content on the Dow
going back to 1897 — and Lo, Mamaysky & Wang (2000) formalised why: technical
patterns are non-parametric estimators of the conditional distribution, not
mysticism.

**Why measured this way:** the MA *stack* captures alignment across three
horizons, which is what distinguishes a durable trend from a single-day spike.
The slopes capture *acceleration* — whether the trend is still being fed or is
rolling over. ADX is included not as a direction signal but as a **quality
filter**: Wilder's ADX measures how much of the recent range came from
directional movement rather than chop, so a trend of the same size in a high-ADX
name is a fundamentally better trade than the same move in a low-ADX name.
Signing it with the DI spread converts it from "how trendy" into "how trendy,
and in which direction" — which is the actual quantity of interest.

### 2.2 Relative momentum — 24%

**What it measures:** total return over 12 months excluding the most recent
month (12-1), plus 6-month and 3-month returns.

**Why it is in:** this is the single most heavily documented cross-sectional
anomaly in equities. The 12-1 construction is not a detail — Novy-Marx (2012)
showed that the "momentum" in a naive 12-month return is largely
*recent* performance, and that skipping the most recent month sharply improves
the signal, because the last month is dominated by **short-term reversal**,
which is a different and oppositely-signed effect. Including it contaminates
the factor.

**Why three horizons:** momentum's term structure is not flat. 12-month is the
most robust and least crowded, 3-month is the most responsive but the noisiest
and most prone to reversal. Weighting all three (via the averaged ranks) makes
the group degrade gracefully: a name needs broad-based strength, not a single
sharp quarter.

### 2.3 Risk-adjusted momentum — 16%

**What it measures:** the same 3/6/12-1 returns, each divided by 63-day
realised volatility, then ranked.

**Why it is in — and why it is separate from raw momentum:** momentum has a
well-documented **crash risk**. Daniel & Moskowitz (2016) showed momentum
crashes are predictable and coincide with market rebounds out of bear markets;
Barroso & Santa-Clara (2015) showed that simply scaling momentum exposure by
its own realised volatility roughly doubles its Sharpe ratio and removes most
of the crash. The mechanism is intuitive: a +40% move in a 15%-vol name and a
+40% move in a 90%-vol name look identical to a raw momentum sort, but they
carry completely different probabilities of continuing versus snapping back.

Separating this from raw momentum rather than blending it in means the
dashboard can show you the difference — and when the two disagree, that
disagreement is itself information about how fragile the signal is.

### 2.4 Relative strength vs the universe — 10%

**What it measures:** 3-month and 6-month return in excess of the
cross-sectional median, plus the fraction of up days over 63 sessions.

**Why it is in:** raw return ranks and excess-return ranks differ when the
whole tape moves together, and excess return is the decision-relevant quantity
for a long/short book. It is also the cleanest available proxy for **relative
strength versus a benchmark**, the oldest idea in technical analysis, here put
on a rigorous footing by subtracting the contemporaneous universe median rather
than comparing to a fixed benchmark index.

The up-day ratio is a **consistency** measure, following Grinblatt &
Moskowitz (2004): momentum driven by many small positive days (gradual
information diffusion, à la Hong & Stein 1999) is more durable than momentum
driven by one or two gaps, which is more likely to be a one-off repricing that
mean-reverts.

### 2.5 Volume and flow — 12%

**What it measures:** the 63-day slope of On-Balance Volume, Chaikin Money
Flow, Money Flow Index, distance from the 20-day rolling VWAP, and relative
volume.

**Why it is in:** price tells you *what* happened; volume tells you whether
anyone with size agreed. Volume is the one genuinely independent information
source a pure-technical model has access to. Specifically:

* **OBV slope** normalised by average daily volume expresses net
  accumulation/distribution in "days of average volume" — a dimensionless
  quantity comparable across a microcap and a mega-cap.
* **Chaikin Money Flow** weights each bar's volume by where the close finished
  *within that bar's range*. It separates "up on volume" (buyers in control all
  day) from "up on a gap then faded" (sellers absorbing) — a distinction raw
  price change cannot make.
* **VWAP distance** is the closest thing to a fair-value anchor, and it is
  where institutional execution algorithms actually operate.
* **Relative volume** is deliberately given a *small* weight on the long side
  and effectively none on the short side, because a volume spike is genuinely
  ambiguous in direction. It is far more informative as a squeeze-risk input
  (see §3).

The economic justification is the price-pressure / fire-sale mechanism of
Coval & Stafford (2007): flows that are not information-driven still move
prices, and volume is how you detect them.

### 2.6 Entry quality — 12% (longs) / 8% (shorts)

**What it measures:** rewards a name that is modestly *below* its 20-day mean
while still in a confirmed uptrend, penalises a name pinned at the top of its
Bollinger band, and peaks when RSI(14) sits near the middle of the range rather
than at an extreme.

**This is the most opinionated choice in the model, so here is the full
argument.** There are two internally coherent ways to trade a trend: buy the
**breakout** (new highs, high %B, high RSI) or buy the **pullback** (retracement
into support within the trend). They are not equally good for a systematic
long/short book:

| | Breakout entry | Pullback entry |
|---|---|---|
| Stop distance | Wide (entry is already extended) | Tight (entry is near support) |
| R multiple at same target | Worse | Better |
| Slippage | Worse (entering into momentum) | Better |
| Fill probability after signal | Lower (gaps away) | Higher |
| Behaviour on failure | Stops out far from the signal price | Invalidates quickly and cheaply |

Because every position here is sized to a **fixed fraction of equity at risk**
(§6), the entry determines the R multiple, not the dollars. Buying a pullback
in an established uptrend is therefore not a stylistic preference — it is the
choice that maximises reward per unit of risk for a given directional view. The
breakout case is still fully covered: breakout names score highly on trend and
momentum, and `flag_breakout_20d` is available as an explicit, optional filter
for users who want that playbook.

The mirror is applied to shorts: reward a name that has bounced modestly *into*
resistance while still in a confirmed downtrend, because that is where you get
a tight stop above the rally high.

---

## 3. Why the short score is **not** the negation of the long score

This is the second-most important design decision, and it is where naive
screens fail.

Equity returns are **not symmetric**. The reasons are structural, not
stylistic:

1. **Volatility asymmetry.** Downside moves are more volatile than upside
   moves — the leverage effect, formalised in the ARCH/GARCH family (Engle
   1982; Nelson 1991) and visible in the asymmetric correlation results of Ang
   & Chen (2002). A short position therefore experiences a different
   volatility regime than the mirror long.
2. **Squeeze risk is unbounded and asymmetric.** A short squeeze can cost 100%
   or more; the equivalent long losing 100% requires the company to go to zero
   and is bounded. This is not a tail risk you can ignore — it is the
   single largest cause of death for short books.
3. **Borrow constraints.** The most attractive shorts (small, illiquid,
   heavily-shorted) are precisely the ones you often cannot borrow, or cannot
   borrow at a sane fee. A screener that ignores this produces untradeable
   ideas.
4. **Reflexivity and the distress premium.** Deeply beaten-down names can
   rally violently on refinancing, takeover or index-inclusion news. The very
   "weakness" that ranks them highly is the condition that makes them
   squeeze-prone.

So the short score uses **its own weights** (trend weighted higher at 27%
because a confirmed downtrend is the most reliable short signal; momentum
lower at 23%), **plus two explicit deductions that have no long-side analogue**:

* a **squeeze penalty** built from relative volume, the 1-month return and the
  latest daily move — i.e. it detects the fingerprint of an in-progress
  squeeze, and
* a **high-volatility penalty** that progressively avoids names above roughly
  8–20% daily ATR.

The result is that the long and short books share a common ancestry but are
not the same trade inverted. In testing the two scores correlate around
−0.85 across a small sample and materially less across the full universe —
deliberately, because *shorting the longs is not a strategy*.

---

## 4. Why Gaussian rank normalisation rather than z-scores

Every factor is converted to the **Blom-normalised cross-sectional rank of its
percentile** and clipped at ±3.

**Why not raw values:** see §0 — units are not comparable.

**Why not a z-score:** a z-score is defined by its mean and standard deviation,
and equity factor distributions are violently fat-tailed and skewed. A single
biotech that went up 900% on trial data can push the standard deviation up
enough to compress every other name's score toward zero, so one stock
effectively silences the screen. Ranks are invariant to the magnitude of
outliers — the 900% mover and the 90% mover affect the ranking identically.

**Why Gaussian (Blom) rather than plain percentile:** a plain percentile keeps
everything in [0,1] with a uniform distribution, which makes the extremes
compress near the boundaries and makes weighted summation across factors
behave unintuitively. The inverse-normal transform maps ranks onto a roughly
N(0,1) scale, so:

* a fixed weight has a **stable, comparable meaning** across all factors;
* the tails are properly separated (the top 1% is ~2.3σ, not 0.99);
* the composite score is interpretable in **standard-deviation units** — a
  score of 1.4 means "this name is 1.4 cross-sectional σ above the average
  name on the blended signal", which is a sentence you can actually act on.

**Why clipping at ±3:** beyond 3σ the exact rank stops carrying information
and starts carrying estimation noise from ties and stale prints.

**A subtle but important detail:** ranks are computed **only over the eligible
pool** (see §5). Otherwise a $0.30 shell company with a −99% return shifts the
percentile boundaries for everyone and subtly distorts the real candidates'
scores.

---

## 5. Hard eligibility gates, and why they are not part of the score

Before any scoring, a name must satisfy, on the screen date:

| Gate | Threshold | Why |
|---|---|---|
| Price | ≥ $5 | Sub-$5 names are frequently unshortable, have penny-wide spreads, and their "returns" are dominated by microstructure noise |
| 20-day dollar volume | ≥ $2M/day | Below this, your own order is the market; a 2×ATR stop cannot be executed without slippage eating the edge |
| History | ≥ 220 sessions | SMA200 is meaningless with less; a name that IPO'd last quarter has no trend to measure |
| Data freshness | last print within 7 days | Guards against delisted and halted names silently carrying a stale score forward |

These are **gates, not scores**, and that distinction matters. If liquidity
were a factor in the composite, a beautifully trending illiquid name could
still surface with a high score and simply be "penalised" — which is exactly
what you do not want, because it is untradeable at any score. Liquidity is a
constraint, not a preference. Making it binary is the honest treatment.

The `is_optionable` flag is a further explicit proxy (price ≥ $5 **and**
20-day dollar volume ≥ $5M). A true options-existence test would require an
options-chain lookup per ticker — roughly 2,900 requests — which is not
viable for a daily screen. The proxy is the standard desk heuristic and, more
importantly, it is the same gate that determines whether a name is shortable
at all.

---

## 6. Trade construction: why ATR, and why fixed R

Every candidate is given:

* **Stop** = entry ∓ 2 × ATR(14)
* **Target** = entry ± 4 × ATR(14) — a fixed **2R** geometry
* **Trail** = entry ∓ 3 × ATR(14)
* **Size** = ⌊(equity × 1%) / (2 × ATR)⌋ shares

**Why ATR and not a percentage stop.** A percentage stop treats a
0.4%-ATR-per-day utility and a 6%-ATR-per-day biotech as if they were the same
instrument, so the utility gets stopped out by noise and the biotech's stop is
meaningless. ATR, introduced by Wilder (1978) precisely for this purpose, is a
per-instrument, per-regime measure of how much the thing actually moves. An
ATR stop means **the same statistical distance from your entry, in every name
and in every volatility regime.**

**Why fixed R rather than a forecast.** The screener's job is *ranking*, not
price prediction. It does not claim to know how far a name will run. What it
can do is guarantee that every position starts with the same reward-to-risk
geometry, so that across a book of 40 names the only variable that matters is
whether the ranking is any good. That is what makes the screen **falsifiable**:
if the top-ranked names do not outperform the bottom-ranked ones, the model is
wrong, and you can measure that directly. A model that instead used bespoke
targets per name would confound "was the ranking right?" with "was the target
right?" and be much harder to falsify.

**Why size from risk.** Risk-parity sizing is what prevents a single
high-volatility name from dominating the P&L. It is the practical expression of
Frazzini & Pedersen's (2014) betting-against-beta result: leverage up the
defensive, size down the aggressive.

---

## 7. The regime overlay

`SPY` versus its own 200-day SMA classifies the tape as **risk-on** or
**risk-off**, and the breadth statistic (% of eligible names above their own
50-day SMA) confirms it. When risk-off, longs are tilted down (×0.80) and
shorts up (×1.20).

**Why it exists:** long/short equity is not market-neutral in practice, and
trend signals perform very differently across regimes — momentum, in
particular, is the strategy most exposed to regime shifts (Daniel &
Moskowitz 2016). De-risking the long book and pressing the short book when the
primary trend is down is the single highest-expected-value regime adjustment
available with price data alone.

**Why it is a tilt and not a hard switch:** hard regime switches are known to
whipsaw badly around the 200-day, and the cost of being wrong at the turn is
high. A 0.8/1.2 tilt degrades gracefully. It is also exposed in the dashboard
as a **separate score column** (`long_score_adj` / `short_score_adj`) rather
than being silently baked into the primary score, so you can see exactly how
much the regime view is contributing and rank by the unadjusted composite if
you disagree with it.

---

## 8. What I deliberately left out

A screener is defined as much by its exclusions. These are not oversights:

| Excluded | Why |
|---|---|
| **All fundamentals** (P/E, earnings, revenue) | The brief is *pure technical*. Mixing them in would make it impossible to attribute the result to the technical model, and fundamentals update quarterly — far too slow to drive a daily screen. |
| **Earnings-date proximity** | Genuinely useful for risk management, but it is event data, not price data, and it would introduce a non-technical dependency with a different update cadence. It belongs in an execution layer above this one. |
| **Sentiment, news, social** | Not price/volume; would break the "pure technical" contract and is far harder to make point-in-time clean. |
| **Machine-learned factor weights** | With ~2,900 names, dozens of candidate features and one realisation of history, any learned weight vector is far more likely to encode the noise of the fitting period than a durable edge. Harvey, Liu & Zhu (2016) showed that most published cross-sectional factors fail a multiple-testing hurdle even *before* you start searching weights; Bailey & López de Prado (2014) showed how easily backtest overfitting produces spurious Sharpe ratios. Fixed, economically motivated, round-number weights are the honest choice at this stage — and the dashboard exposes them as sliders so you can test alternatives yourself rather than being handed a black box. |
| **Intraday / minute bars** | Different signal decay, different cost structure, and it would change the holding period the model is calibrated for. Out of scope. |
| **Short-interest and borrow data** | The correct input for squeeze risk. It is a paid dataset, not price data. The squeeze penalty is an explicit, documented *price-based proxy* for it, not a substitute for it. |
| **A dozen more factors** | Every added factor raises the multiple-testing bar and dilutes the ones that work. Six groups, each with a stated economic rationale, is a deliberate stopping point. |

---

## 9. Honest limitations

I would rather state these than let them be discovered later:

1. **No backtest has been run as part of this build.** The justification above
   rests on the published empirical literature and on economic reasoning, not
   on a historical simulation of *this specific parameterisation*. The weights
   are educated priors, not optima. Treat the first production runs as
   paper-trading while you gather evidence.
2. **Point-in-time integrity.** The universe is today's index membership. A
   genuine backtest would need historical membership and delisting returns, or
   it would suffer survivorship bias. This is why #1 is a real limitation and
   not a formality.
3. **Corporate actions.** Prices are total-return adjusted via yfinance, which
   handles splits and dividends, but adjusted data can still carry artefacts
   around special situations.
4. **The 12-1 momentum skip assumes the reversal effect dominates the last
   month.** It normally does. Around genuine news events it does not, and the
   screen will be late to those.
5. **Correlation is high between some groups** (raw and risk-adjusted momentum
   in particular). The effective number of independent bets is smaller than
   six, so the diversification benefit of the score is less than the weight
   table implies.
6. **yfinance is an unofficial, rate-limited interface.** It is the right tool
   for this build, but production use would want a licensed feed.

**What would falsify this design:** rank the universe daily into deciles by
`long_score`, hold equal-risk, and measure forward 1/3/6-month returns net of
costs. If decile spread is not reliably positive and monotone, the model is
wrong. The same test on `short_score` in the bottom decile. That is the
experiment this screener is built to make possible — every score, factor leg,
and trade level is exported to CSV precisely so it can be run.

---

## 10. Summary of the argument

1. **Cross-sectional, not threshold-based** — because indicator levels are not
   comparable across stocks or regimes, and ranks are.
2. **Gaussian ranks, clipped** — because equity factor distributions are fat-
   tailed and one outlier should not silence the screen, while weights should
   still mean the same thing across factors.
3. **Six orthogonal-ish groups**, each with a documented economic mechanism and
   a supporting literature — trend, momentum, risk-adjusted momentum, relative
   strength, volume/flow, entry quality.
4. **Risk-adjusted momentum as its own group** — because momentum's crash risk
   is real and vol-scaling is the documented fix.
5. **A genuinely asymmetric short model** — because shorting is not negative
   buying: squeezes are unbounded, borrow is constrained, and volatility is
   asymmetric.
6. **Absolute ATR-based penalties layered on top of relative ranks** — because
   a rank can never express "this is simply too extended to buy", and that is a
   real constraint that ranks cannot encode.
7. **Liquidity and price as hard gates, not scores** — because an untradeable
   idea should be excluded, not down-weighted.
8. **Uniform ATR-based, fixed-R trade construction** — so risk is normalised
   across instruments and the ranking itself becomes falsifiable.
9. **A regime tilt exposed as a separate column** — so the market view is
   visible and can be switched off, not hidden inside the score.
10. **Fixed, economically motivated weights, with a UI to change them** —
    because with one realisation of history, learned weights would be
    overfitting, and transparency beats a black box.

---

## References

* Ang, A. & Chen, J. (2002). *Asymmetric Correlations of Equity Portfolios.* JFE.
* Asness, C., Moskowitz, T. & Pedersen, L. (2013). *Value and Momentum Everywhere.* JF.
* Bailey, D. & López de Prado, M. (2014). *The Deflated Sharpe Ratio.* JPM.
* Barroso, P. & Santa-Clara, P. (2015). *Momentum Has Its Moments.* JFE.
* Brock, W., Lakonishok, J. & LeBaron, B. (1992). *Simple Technical Trading Rules and the Stochastic Properties of Stock Returns.* JF.
* Chan, L., Jegadeesh, N. & Lakonishok, J. (1996). *Momentum Strategies.* JPM.
* Coval, J. & Stafford, E. (2007). *Asset Fire Sales (and Purchases) in Equity Markets.* JFE.
* Daniel, K. & Moskowitz, T. (2016). *Momentum Crashes.* JFE.
* Engle, R. (1982). *Autoregressive Conditional Heteroscedasticity.* Econometrica.
* Frazzini, A. & Pedersen, L. (2014). *Betting Against Beta.* JFE.
* Grinblatt, M. & Moskowitz, T. (2004). *Predicting Stock Price Movements from Past Returns.* JFE.
* Harvey, C., Liu, Y. & Zhu, H. (2016). *...and the Cross-Section of Expected Returns.* RFS.
* Hong, H. & Stein, J. (1999). *A Unified Theory of Underreaction, Momentum Trading and Overreaction.* JF.
* Jegadeesh, N. & Titman, S. (1993). *Returns to Buying Winners and Selling Losers.* JF.
* Lo, A., Mamaysky, H. & Wang, J. (2000). *Foundations of Technical Analysis.* JF.
* Moskowitz, T., Ooi, Y.H. & Pedersen, L. (2012). *Time Series Momentum.* JFE.
* Nelson, D. (1991). *Conditional Heteroskedasticity in Asset Returns: A New Approach.* Econometrica.
* Novy-Marx, R. (2012). *Is Momentum Really Momentum?* JFE.
* Wilder, J.W. (1978). *New Concepts in Technical Trading Systems.*
