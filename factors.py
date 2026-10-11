"""
Factor model, cross-sectional scoring, flag generation and trade construction.

Design in one paragraph
-----------------------
Raw indicator values are not comparable across stocks or across time: an RSI of
62 means something different for a 0.5-ATR-per-day utility than for a 6-ATR
biotech, and a 30% six-month return is strong in a flat tape and mediocre in a
melt-up.  So every continuous factor is converted to a **cross-sectional
Gaussian rank** on the screen date.  That makes all factors dimensionless,
outlier-robust, regime-neutral and directly summable.  Six orthogonal factor
groups are then combined with fixed weights into a long score and a separately
specified short score.  The short side is deliberately *not* the negation of
the long side, because equity downside is structurally different (volatility
asymmetry, squeeze risk, borrow constraints).

See TECHNICAL_JUSTIFICATION.md for the full rationale.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I


# ==========================================================================
# Cross-sectional normalisation
# ==========================================================================
def _ndtri(p: np.ndarray) -> np.ndarray:
    """Vectorised inverse standard-normal CDF (Acklam's rational approximation).

    Max relative error ~1.15e-9, which is far beyond what factor ranking needs,
    and it removes any dependency on scipy.
    """
    p = np.clip(np.asarray(p, dtype="float64"), 1e-12, 1 - 1e-12)
    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)

    plow, phigh = 0.02425, 1 - 0.02425
    out = np.zeros_like(p)

    lo = p < plow
    hi = p > phigh
    mid = ~(lo | hi)

    if lo.any():
        q = np.sqrt(-2 * np.log(p[lo]))
        out[lo] = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                  ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if hi.any():
        q = np.sqrt(-2 * np.log(1 - p[hi]))
        out[hi] = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                   ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if mid.any():
        q = p[mid] - 0.5
        r = q * q
        out[mid] = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
                   (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    return out


def cs_gauss_rank(s: pd.Series) -> pd.Series:
    """Blom-normalised cross-sectional rank -> approximately N(0,1).

    Outlier-robust (a single 900% mover cannot distort the scale, unlike a
    z-score), bounded, and monotone in the raw factor.
    """
    v = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    n = int(v.notna().sum())
    out = pd.Series(np.nan, index=v.index, dtype="float64")
    if n < 8:
        return out
    r = v.rank(method="average")
    p = (r - 0.375) / (n + 0.25)
    out.loc[v.notna()] = _ndtri(p[v.notna()].values)
    return out.clip(-C.WINSOR_Z, C.WINSOR_Z)


def cs_zscore(s: pd.Series) -> pd.Series:
    v = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    mu, sd = v.mean(), v.std()
    if not np.isfinite(sd) or sd == 0:
        return pd.Series(0.0, index=v.index)
    return ((v - mu) / sd).clip(-C.WINSOR_Z, C.WINSOR_Z)


def _wsum(parts: dict, weights: dict) -> pd.Series:
    """Weighted sum of already-normalised components, renormalising on NaN."""
    num = None
    den = None
    for k, w in weights.items():
        c = parts.get(k)
        if c is None:
            continue
        c = c.astype("float64")
        contrib = c * w
        mask = c.notna().astype("float64") * w
        num = contrib if num is None else num.add(contrib, fill_value=0.0)
        den = mask if den is None else den.add(mask, fill_value=0.0)
    if num is None:
        return pd.Series(np.nan, index=next(iter(parts.values())).index)
    return num / den.replace(0.0, np.nan)


def _ramp(x: pd.Series, start: float, full: float) -> pd.Series:
    """0 below ``start``, linearly ramping to 1 at ``full``, clipped to [0,1]."""
    return ((x - start) / (full - start)).clip(0.0, 1.0)


# ==========================================================================
# Feature construction
# ==========================================================================
class _Snap:
    """Accumulates the *last valid* value of each wide frame, per ticker."""

    def __init__(self, index: pd.Index, columns: pd.Index, last_pos: np.ndarray):
        self._cols = columns
        self._pos = last_pos
        self._j = np.arange(len(columns))
        self.data: dict[str, pd.Series] = {}

    def add(self, name: str, wide) -> pd.Series:
        if isinstance(wide, pd.Series):
            wide = wide.to_frame()
        arr = np.asarray(wide.values, dtype="float64")
        if arr.shape[1] != len(self._cols):
            s = pd.Series(np.nan, index=self._cols, dtype="float64")
            self.data[name] = s
            return s
        p = np.clip(self._pos, 0, max(0, arr.shape[0] - 1))
        vals = arr[p, self._j]
        s = pd.Series(vals, index=self._cols, dtype="float64")
        s[self._pos < 0] = np.nan
        self.data[name] = s
        return s

    def add_bool(self, name: str, wide) -> pd.Series:
        s = self.add(name, wide.astype("float64") if hasattr(wide, "astype") else wide)
        self.data[name] = s.fillna(0.0) > 0.5
        return self.data[name]


def compute_features(prices: dict, verbose: bool = True) -> pd.DataFrame:
    """Compute the full feature snapshot: one row per ticker, latest values."""
    close = prices["close"]
    high = prices["high"].where(prices["high"].notna(), close)
    low = prices["low"].where(prices["low"].notna(), close)
    volume = prices["volume"].fillna(0.0)

    cols = close.columns
    last_valid = close.apply(lambda s: s.last_valid_index())
    max_date = close.index.max()
    last_pos = close.index.get_indexer(pd.DatetimeIndex(last_valid))

    snap = _Snap(close.index, cols, last_pos)

    # ---- data quality -----------------------------------------------------
    bars = close.notna().sum()
    stale_days = pd.Series(
        [(max_date - d).days if pd.notna(d) else 9999 for d in last_valid], index=cols
    )
    snap.data["bars"] = bars.astype("float64")
    snap.data["stale_days"] = stale_days.astype("float64")
    snap.data["last_date"] = pd.Series(
        [d.strftime("%Y-%m-%d") if pd.notna(d) else "" for d in last_valid], index=cols
    )

    # ---- core levels ------------------------------------------------------
    px = snap.add("close", close)
    prev_close = snap.add("prev_close", close.shift(1))
    snap.data["chg_1d_pct"] = (px / prev_close - 1.0) * 100.0
    snap.add("high", high)
    snap.add("low", low)

    a14 = I.atr(high, low, close, C.ATR_PERIOD)
    atr = snap.add("atr14", a14)
    snap.data["atr_pct"] = (atr / px.replace(0, np.nan)) * 100.0

    s20 = I.sma(close, C.SMA_FAST)
    s50 = I.sma(close, C.SMA_MID)
    s200 = I.sma(close, C.SMA_SLOW)
    e21 = I.ema(close, C.EMA_MID)
    for nm, f in (("sma20", s20), ("sma50", s50), ("sma200", s200), ("ema21", e21)):
        snap.add(nm, f)

    for nm, ma in (("sma20", s20), ("sma50", s50), ("sma200", s200), ("ema21", e21)):
        d = (close / ma - 1.0) * 100.0
        snap.add("dist_" + nm + "_pct", d.replace([np.inf, -np.inf], np.nan))

    snap.add_bool("above_sma20", close > s20)
    snap.add_bool("above_sma50", close > s50)
    snap.add_bool("above_sma200", close > s200)

    # ---- trend quality ----------------------------------------------------
    snap.add("sma50_slope", I.slope_pct(s50, 63))
    snap.add("sma200_slope", I.slope_pct(s200, 126))

    adx14, pdi, mdi = I.adx(high, low, close, C.ADX_PERIOD)
    adxv = snap.add("adx14", adx14)
    snap.add("plus_di", pdi)
    snap.add("minus_di", mdi)
    # signed trend strength: magnitude from ADX, direction from the DI spread
    snap.add("di_spread", pdi - mdi)
    snap.data["adx_signed"] = (adxv / 100.0) * np.tanh(
        snap.data["di_spread"].fillna(0.0) / 10.0
    )

    stack = (close > s20).astype(float) + (s20 > s50).astype(float) + (s50 > s200).astype(float)
    stack = stack.where(s200.notna(), np.nan)
    snap.add("ma_stack", stack * (2.0 / 3.0) - 1.0)   # -> -1 .. +1

    macd_line, macd_sig, macd_hist = I.macd(close)
    snap.add("macd_hist_pct", (macd_hist / px.replace(0, np.nan)) * 100.0)
    snap.add_bool("flag_macd_bull_cross", _crossed_up(macd_line, macd_sig, 10))
    snap.add_bool("flag_macd_bear_cross", _crossed_up(macd_sig, macd_line, 10))

    snap.add_bool("flag_golden_cross", _crossed_up(s50, s200, 20))
    snap.add_bool("flag_death_cross", _crossed_up(s200, s50, 20))

    # ---- momentum ---------------------------------------------------------
    r21 = I.roc(close, 21)
    r63 = I.roc(close, 63)
    r126 = I.roc(close, 126)
    r252 = I.roc(close, 252)
    r12_1 = I.momentum_12_1(close)
    for nm, f in (("ret_1m", r21), ("ret_3m", r63), ("ret_6m", r126),
                  ("ret_12m", r252), ("ret_12_1", r12_1)):
        snap.add(nm, f * 100.0)
    snap.add("up_day_ratio_63", I.up_day_ratio(close, 63))

    # ---- volatility & risk adjustment -------------------------------------
    v21 = I.realized_vol(close, C.VOLATILITY_WINDOWS[0])
    v63 = I.realized_vol(close, C.VOLATILITY_WINDOWS[1])
    snap.add("vol_21", v21 * 100.0)
    snap.add("vol_63", v63 * 100.0)
    v63s = v63.replace(0, np.nan)
    snap.add("mom6_over_vol", (r126 / v63s))
    snap.add("mom3_over_vol", (r63 / v63s))
    snap.add("mom12_1_over_vol", (r12_1 / v63s))
    snap.add("vol_pctile", I.rolling_percentile(v21, 252))

    # ---- relative strength (vs the universe, computed cross-sectionally) --
    rets = {"ret_1m": snap.data["ret_1m"], "ret_3m": snap.data["ret_3m"],
            "ret_6m": snap.data["ret_6m"]}
    for nm, s in rets.items():
        snap.data["rs_" + nm] = s - s.median()

    # ---- oscillators / mean reversion -------------------------------------
    rsi14_w = I.rsi(close, C.RSI_PERIOD)
    rsi2_w = I.rsi(close, C.RSI_SHORT_PERIOD)
    rvol_w = I.rvol(volume)
    snap.add("rsi14", rsi14_w)
    snap.add("rsi2", rsi2_w)
    k, d = I.stochastic(high, low, close, C.STOCH_PERIOD)
    snap.add("stoch_k", k)
    snap.add("williams_r", I.williams_r(high, low, close, C.STOCH_PERIOD))
    mid_b, up_b, lo_b, pct_b, bbw = I.bollinger(close, C.BB_PERIOD, C.BB_STD)
    snap.add("pct_b", pct_b)
    bbw_v = snap.add("bbw", bbw)
    bbw_pct_w = I.rolling_percentile(bbw, C.BBW_LOOKBACK)
    snap.add("bbw_pctile", bbw_pct_w)
    snap.add("zscore_20", I.zscore(close, 20))
    snap.add("stretch_atr_20", I.atr_stretch(close, s20, a14))
    snap.add("stretch_atr_50", I.atr_stretch(close, s50, a14))
    snap.add("stretch_atr_200", I.atr_stretch(close, s200, a14))

    # ---- 52-week context ---------------------------------------------------
    hh252 = high.rolling(252, min_periods=120).max()
    ll252 = low.rolling(252, min_periods=120).min()
    snap.add("hi_52w", hh252)
    snap.add("lo_52w", ll252)
    snap.add("range_pos_52w", I.range_position(close, high, low, 252) * 100.0)
    snap.data["pct_to_52w_high"] = (px / snap.data["hi_52w"] - 1.0) * 100.0
    snap.data["pct_above_52w_low"] = (px / snap.data["lo_52w"] - 1.0) * 100.0

    # ---- volume / flow -----------------------------------------------------
    snap.add("rvol", rvol_w)
    snap.add("obv_slope_63", I.obv_slope(close, volume, C.OBV_SLOPE_WINDOW))
    snap.add("cmf_21", I.cmf(high, low, close, volume, C.CMF_PERIOD))
    snap.add("mfi14", I.mfi(high, low, close, volume, C.MFI_PERIOD))
    snap.add("ad_line_slope", I.slope_pct(I.ad_line(high, low, close, volume), 63))
    vwap20 = I.rolling_vwap(high, low, close, volume, 20)
    snap.add("vwap20_dist_pct", (close / vwap20 - 1.0) * 100.0)

    dv20 = I.dollar_volume(close, volume, 20)
    snap.add("avg_dollar_vol_20", dv20)
    snap.add("avg_dollar_vol_60", I.dollar_volume(close, volume, 60))
    snap.data["avg_dollar_vol_m"] = snap.data["avg_dollar_vol_20"] / 1e6

    # ---- ranges / breakout -------------------------------------------------
    dh, dl = I.donchian(high, low, C.DONCHIAN_PERIOD)
    prev_dh = dh.shift(1)
    prev_dl = dl.shift(1)
    snap.add_bool("flag_breakout_20d", close >= prev_dh)
    snap.add_bool("flag_breakdown_20d", close <= prev_dl)
    snap.add_bool("flag_new_high_252", close >= hh252.shift(1))
    snap.add_bool("flag_new_low_252", close <= ll252.shift(1))

    # ---- discrete flags ----------------------------------------------------
    snap.add_bool("flag_uptrend", (close > s50) & (s50 > s200))
    snap.add_bool("flag_downtrend", (close < s50) & (s50 < s200))
    snap.add_bool("flag_above_200sma", close > s200)
    snap.add_bool("flag_rsi_oversold", rsi14_w < 30)
    snap.add_bool("flag_rsi_overbought", rsi14_w > 70)
    snap.add_bool("flag_rsi2_extreme", (rsi2_w < 5) | (rsi2_w > 95))
    snap.add_bool("flag_bb_squeeze", bbw_pct_w < 0.20)
    snap.add_bool("flag_volume_spike", rvol_w > 2.0)
    snap.add_bool("flag_near_52w_high", (close / hh252 - 1.0) > -0.02)
    snap.add_bool("flag_near_52w_low", (close / ll252 - 1.0) < 0.02)
    snap.add_bool("flag_gap_up_today",
                  (prices["open"] / close.shift(1).replace(0, np.nan) - 1.0) > 0.03)

    # Release the large intermediate wide frames before building the result.
    # Each one is (days x tickers) x 8 bytes ~= 11.5 MB at full universe size,
    # and ~45 of them would otherwise stay alive for the rest of the function.
    del close, high, low, volume, prev_close, a14, atr, s20, s50, s200, e21
    del adx14, pdi, mdi, adxv, stack, macd_line, macd_sig, macd_hist
    del r21, r63, r126, r252, r12_1, v21, v63, v63s, rets, k, d
    del mid_b, up_b, lo_b, pct_b, bbw, bbw_v, hh252, ll252
    del rsi14_w, rsi2_w, rvol_w, bbw_pct_w
    del dv20, vwap20, dh, dl, prev_dh, prev_dl, last_valid, stale_days

    df = pd.DataFrame(snap.data, index=cols)
    if verbose:
        print(f"  features: {df.shape[1]} columns x {df.shape[0]} tickers")
    return df


def _crossed_up(fast: pd.DataFrame, slow: pd.DataFrame, within: int) -> pd.DataFrame:
    """True if ``fast`` crossed above ``slow`` at any point in the last
    ``within`` bars and is currently still above."""
    cross = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    return (cross.rolling(within, min_periods=1).max() > 0) & (fast > slow)


# ==========================================================================
# Scoring
# ==========================================================================
def _avg(*series, index=None) -> pd.Series:
    """Average several already-normalised, identically-signed component scores."""
    parts = [s for s in series if s is not None and len(s)]
    if not parts:
        return pd.Series(np.nan, index=index if index is not None else [], dtype="float64")
    return pd.concat(parts, axis=1).mean(axis=1, skipna=True)


def score_universe(feat: pd.DataFrame, meta: pd.DataFrame,
                    verbose: bool = True) -> pd.DataFrame:
    """Turn the feature snapshot into ranked long and short candidates."""
    # Only score actual index members: the price cache also carries benchmark
    # and reference series (SPY, IWM, QQQ, TLT) that must never be screened.
    known = set(meta["ticker"].astype(str))
    df = feat.loc[[t for t in feat.index if t in known]].copy()

    # ------------------------------------------------------------------
    # 0. Eligibility gates (liquidity, price, history, staleness)
    # ------------------------------------------------------------------
    eligible = (
        (df["close"] >= C.MIN_PRICE)
        & (df["close"] <= C.MAX_PRICE)
        & (df["avg_dollar_vol_20"] >= C.MIN_DOLLAR_VOLUME)
        & (df["bars"] >= C.MIN_BARS_REQUIRED)
        & (df["stale_days"] <= 7)
        & df["sma200"].notna()
        & df["atr14"].notna()
        & (df["atr14"] > 0)
    )
    df["eligible"] = eligible.fillna(False)

    # The cross-sectional ranks should be computed on the *eligible* pool,
    # otherwise a $0.30 shell company can shift the percentile boundaries.
    rank_pool = df[df["eligible"]]
    pool_idx = rank_pool.index

    def rk(col=None, sign=1.0, series=None):
        """Gaussian rank of a factor, computed on the eligible pool only."""
        if series is not None:
            s = pd.to_numeric(series, errors="coerce") * sign
        elif col in rank_pool.columns:
            s = rank_pool[col] * sign
        else:
            return None
        out = pd.Series(np.nan, index=df.index, dtype="float64")
        out.loc[pool_idx] = cs_gauss_rank(s.loc[pool_idx])
        return out

    # ------------------------------------------------------------------
    # 1. Factor groups
    # ------------------------------------------------------------------
    # --- trend quality (long orientation: higher = cleaner uptrend)
    trend_long = _avg(rk("dist_sma50_pct"), rk("dist_sma200_pct"),
                      rk("sma50_slope"), rk("sma200_slope"),
                      rk("adx_signed"), rk("ma_stack"))
    # --- trend quality (short orientation: higher = cleaner downtrend)
    trend_short = _avg(rk("dist_sma50_pct", -1), rk("dist_sma200_pct", -1),
                       rk("sma50_slope", -1), rk("sma200_slope", -1),
                       rk("adx_signed", -1), rk("ma_stack", -1))

    # --- relative momentum (12-1 dominates: it is the most robust horizon)
    mom_long = _avg(rk("ret_12_1"), rk("ret_6m"), rk("ret_3m"))
    mom_short = _avg(rk("ret_12_1", -1), rk("ret_6m", -1), rk("ret_3m", -1))

    # --- risk-adjusted momentum (return per unit of realised volatility)
    ram_long = _avg(rk("mom6_over_vol"), rk("mom3_over_vol"), rk("mom12_1_over_vol"))
    ram_short = _avg(rk("mom6_over_vol", -1), rk("mom3_over_vol", -1),
                     rk("mom12_1_over_vol", -1))

    # --- relative strength vs the whole universe
    rs_long = _avg(rk("rs_ret_3m"), rk("rs_ret_6m"), rk("up_day_ratio_63"))
    rs_short = _avg(rk("rs_ret_3m", -1), rk("rs_ret_6m", -1), rk("up_day_ratio_63", -1))

    # --- volume / flow confirmation
    vol_long = _avg(rk("obv_slope_63"), rk("cmf_21"), rk("mfi14"),
                    rk("vwap20_dist_pct"), rk("rvol"))
    vol_short = _avg(rk("obv_slope_63", -1), rk("cmf_21", -1), rk("mfi14", -1),
                     rk("vwap20_dist_pct", -1))

    # --- entry quality: reward the *shallow pullback inside a trend* rather
    #     than the extended breakout (better R multiple, tighter stop).
    entry_long = _avg(rk("stretch_atr_20", -1),
                      rk(series=-(df["rsi14"] - 55.0).abs()),
                      rk("pct_b", -1))
    entry_short = _avg(rk("stretch_atr_20"),
                       rk(series=-(df["rsi14"] - 45.0).abs()),
                       rk("pct_b"))

    # --- short-squeeze / asymmetry penalty (shorts only)
    squeeze_raw = (
        _ramp(df["rvol"], 1.5, 4.0) * 0.45
        + _ramp(df["ret_1m"], 5.0, 40.0) * 0.35
        + _ramp(df["chg_1d_pct"], 3.0, 12.0) * 0.20
    ).fillna(0.0)
    squeeze_pen = rk(series=squeeze_raw)

    df["f_trend_long"] = trend_long
    df["f_trend_short"] = trend_short
    df["f_momentum_long"] = mom_long
    df["f_momentum_short"] = mom_short
    df["f_riskadj_long"] = ram_long
    df["f_riskadj_short"] = ram_short
    df["f_relstrength_long"] = rs_long
    df["f_relstrength_short"] = rs_short
    df["f_volume_long"] = vol_long
    df["f_volume_short"] = vol_short
    df["f_entry_long"] = entry_long
    df["f_entry_short"] = entry_short
    df["f_squeeze_penalty"] = squeeze_pen

    # ------------------------------------------------------------------
    # 2. Composite scores
    # ------------------------------------------------------------------
    long_parts = {
        "trend": df["f_trend_long"], "momentum": df["f_momentum_long"],
        "risk_adj_mom": df["f_riskadj_long"], "rel_strength": df["f_relstrength_long"],
        "volume": df["f_volume_long"], "entry": df["f_entry_long"],
    }
    short_parts = {
        "trend": df["f_trend_short"], "momentum": df["f_momentum_short"],
        "risk_adj_mom": df["f_riskadj_short"], "rel_strength": df["f_relstrength_short"],
        "volume": df["f_volume_short"], "entry": df["f_entry_short"],
    }
    df["long_score"] = _wsum(long_parts, C.LONG_WEIGHTS)
    df["short_score"] = _wsum(short_parts, C.SHORT_WEIGHTS)

    # --- absolute (not relative) risk controls -------------------------
    # A rank-based factor can never say "this is simply too extended to buy".
    # These two adjustments are deliberately *absolute* and in ATR units.
    overext = C.OVEREXTENSION_MAX_PENALTY * _ramp(
        df["stretch_atr_20"], C.OVEREXTENSION_START_ATR, C.OVEREXTENSION_FULL_ATR
    ).fillna(0.0)
    df["penalty_overextension"] = overext
    df["long_score"] = df["long_score"] - overext

    # Shorts carry unbounded loss risk: fade only reasonably volatile names
    # and never chase a name that is already down 60% (bounce risk).
    short_vol_pen = 0.9 * _ramp(df["atr_pct"], 8.0, 20.0).fillna(0.0)
    short_sq = 0.8 * df["f_squeeze_penalty"].fillna(0.0)
    df["penalty_short_vol"] = short_vol_pen
    df["short_score"] = df["short_score"] - short_vol_pen - short_sq

    df.loc[~df["eligible"], ["long_score", "short_score"]] = np.nan

    # ------------------------------------------------------------------
    # 3. Ranks & percentiles
    # ------------------------------------------------------------------
    df["long_pct"] = df["long_score"].rank(pct=True) * 100.0
    df["short_pct"] = df["short_score"].rank(pct=True) * 100.0
    df["rank_long"] = df["long_score"].rank(ascending=False, method="min")
    df["rank_short"] = df["short_score"].rank(ascending=False, method="min")

    # ------------------------------------------------------------------
    # 4. Trade construction (ATR-normalised)
    # ------------------------------------------------------------------
    a = df["atr14"]
    df["entry"] = df["close"]
    df["stop_long"] = df["close"] - C.STOP_ATR_MULT * a
    df["target_long"] = df["close"] + C.TARGET_ATR_MULT * a
    df["trail_long"] = df["close"] - C.TRAIL_ATR_MULT * a
    df["stop_short"] = df["close"] + C.STOP_ATR_MULT * a
    df["target_short"] = df["close"] - C.TARGET_ATR_MULT * a
    df["trail_short"] = df["close"] + C.TRAIL_ATR_MULT * a
    df["risk_pct"] = (C.STOP_ATR_MULT * a / df["close"].replace(0, np.nan)) * 100.0
    df["reward_risk"] = C.TARGET_ATR_MULT / C.STOP_ATR_MULT
    df["shares_for_1pct_risk"] = np.floor(
        (C.ACCOUNT_EQUITY * C.ACCOUNT_RISK_PCT) / (C.STOP_ATR_MULT * a).replace(0, np.nan)
    )

    # ------------------------------------------------------------------
    # 5. Optionability proxy
    # ------------------------------------------------------------------
    # A true "optionable" test needs an options-chain lookup per ticker
    # (~2,900 requests).  This proxy is the standard desk screen: a listed
    # US equity above $5 with real liquidity almost always carries listed
    # options, and it is the same gate that determines whether a name is
    # shortable at all.
    df["is_optionable"] = (
        (df["close"] >= C.MIN_PRICE)
        & (df["avg_dollar_vol_20"] >= 5_000_000)
    ).fillna(False)

    # ------------------------------------------------------------------
    # 6. Assemble output
    # ------------------------------------------------------------------
    df = df.join(meta.set_index("ticker")[["name", "sector", "universe", "universes"]],
                 how="left")
    df.index.name = "ticker"
    df = df.reset_index()

    flag_cols = sorted([c for c in df.columns if c.startswith("flag_")])
    df["flags"] = df[flag_cols].fillna(False).apply(
        lambda r: ",".join(c.replace("flag_", "") for c, v in r.items() if bool(v)), axis=1
    )
    df["n_flags"] = df[flag_cols].fillna(False).sum(axis=1)
    df.attrs["flag_columns"] = flag_cols

    if verbose:
        print(f"  scored: {int(df['eligible'].sum())} eligible of {len(df)} tickers")
    return df


# ==========================================================================
# Market regime
# ==========================================================================
def market_regime(prices: dict, scored: pd.DataFrame | None = None) -> dict:
    """SPY-vs-200SMA trend regime plus universe breadth."""
    out = {
        "spy_close": np.nan, "spy_sma200": np.nan, "spy_dist_pct": np.nan,
        "regime": "unknown", "breadth_pct": np.nan, "as_of": "",
    }
    close = prices["close"]
    sym = C.BENCHMARK
    if sym not in close.columns:
        return out
    s = close[sym].dropna()
    if len(s) < C.REGIME_SMA:
        return out
    sma = s.rolling(C.REGIME_SMA).mean()
    last, ma = float(s.iloc[-1]), float(sma.iloc[-1])
    out.update(
        spy_close=last, spy_sma200=ma,
        spy_dist_pct=(last / ma - 1.0) * 100.0 if ma else np.nan,
        as_of=str(s.index[-1].date()),
        regime="risk_on" if last > ma else "risk_off",
    )
    if scored is not None and "above_sma50" in scored:
        el = scored[scored["eligible"]]
        if len(el):
            out["breadth_pct"] = float(el["above_sma50"].mean() * 100.0)
    return out


def apply_regime_tilt(scored: pd.DataFrame, regime: dict) -> pd.DataFrame:
    """Optional overlay: de-risk longs / press shorts in a risk-off tape."""
    df = scored.copy()
    risk_off = regime.get("regime") == "risk_off"
    lf = C.REGIME_LONG_TILT if risk_off else 1.0
    sf = C.REGIME_SHORT_TILT if risk_off else 1.0
    df["long_score_adj"] = df["long_score"] * lf
    df["short_score_adj"] = df["short_score"] * sf
    df["rank_long_adj"] = df["long_score_adj"].rank(ascending=False, method="min")
    df["rank_short_adj"] = df["short_score_adj"].rank(ascending=False, method="min")
    return df
