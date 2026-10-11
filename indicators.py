"""
Vectorised technical indicator library.

Every function takes **wide** frames (index = dates, columns = tickers) and
returns wide frames of the same shape.  That is the whole point: computing an
indicator once across ~2,900 tickers at the same time is both far faster and
mathematically identical to doing it ticker-by-ticker, because every indicator
here is a per-column operation.

Implements (Wilder-exact where Wilder defined it):
  SMA / EMA / HMA-free moving averages
  True Range, ATR (Wilder RMA)
  RSI (Wilder), Stochastic, Williams %R
  ADX / +DI / -DI (Wilder)
  MACD
  Bollinger Bands -> %B and normalised bandwidth
  Rate of change / momentum
  Realised volatility (annualised)
  OBV, Accumulation/Distribution, Chaikin Money Flow, Money Flow Index
  Rolling VWAP
  Donchian channels
  Rolling z-score, ATR-normalised stretch, 52-week range position
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C

EPS = 1e-12


# --------------------------------------------------------------------------
# moving averages
# --------------------------------------------------------------------------
def sma(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.rolling(n, min_periods=max(2, n // 2)).mean()


def ema(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.ewm(span=n, adjust=False, min_periods=max(2, n // 2)).mean()


def rma(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Wilder's smoothing (a.k.a. RMA / SMMA)."""
    return df.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


# --------------------------------------------------------------------------
# volatility
# --------------------------------------------------------------------------
def true_range(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    pc = close.shift(1)
    a = (high - low).values
    b = (high - pc).abs().values
    c = (low - pc).abs().values
    # frames are already index/column aligned, so element-wise maximum is safe
    return pd.DataFrame(
        np.fmax(np.fmax(a, b), c), index=high.index, columns=high.columns
    )


def atr(high, low, close, n: int = C.ATR_PERIOD) -> pd.DataFrame:
    return rma(true_range(high, low, close), n)


def realized_vol(close: pd.DataFrame, n: int) -> pd.DataFrame:
    """Annualised close-to-close realised volatility."""
    r = np.log(close / close.shift(1))
    return r.rolling(n, min_periods=max(5, n // 2)).std() * np.sqrt(C.TRADING_DAYS)


# --------------------------------------------------------------------------
# oscillators
# --------------------------------------------------------------------------
def rsi(close: pd.DataFrame, n: int = C.RSI_PERIOD) -> pd.DataFrame:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    au = rma(up, n)
    ad = rma(dn, n)
    rs = au / (ad + EPS)
    return 100.0 - 100.0 / (1.0 + rs)


def stochastic(high, low, close, n: int = C.STOCH_PERIOD, d: int = 3):
    hh = high.rolling(n, min_periods=n // 2).max()
    ll = low.rolling(n, min_periods=n // 2).min()
    k = 100.0 * (close - ll) / (hh - ll + EPS)
    return k, k.rolling(d, min_periods=1).mean()


def williams_r(high, low, close, n: int = C.STOCH_PERIOD) -> pd.DataFrame:
    hh = high.rolling(n, min_periods=n // 2).max()
    ll = low.rolling(n, min_periods=n // 2).min()
    return -100.0 * (hh - close) / (hh - ll + EPS)


def adx(high, low, close, n: int = C.ADX_PERIOD):
    """Return (adx, plus_di, minus_di) using Wilder's original formulation."""
    up = high.diff()
    dn = -low.diff()
    plus_dm = pd.DataFrame(
        np.where((up > dn) & (up > 0), up, 0.0), index=high.index, columns=high.columns
    )
    minus_dm = pd.DataFrame(
        np.where((dn > up) & (dn > 0), dn, 0.0), index=high.index, columns=high.columns
    )
    tr = true_range(high, low, close)
    atr_ = rma(tr, n)
    pdi = 100.0 * rma(plus_dm, n) / (atr_ + EPS)
    mdi = 100.0 * rma(minus_dm, n) / (atr_ + EPS)
    dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi + EPS)
    return rma(dx, n), pdi, mdi


def macd(close: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal // 2).mean()
    return line, sig, line - sig


# --------------------------------------------------------------------------
# bands & channels
# --------------------------------------------------------------------------
def bollinger(close: pd.DataFrame, n: int = C.BB_PERIOD, k: float = C.BB_STD):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=max(5, n // 2)).std()
    upper = mid + k * sd
    lower = mid - k * sd
    pct_b = (close - lower) / (upper - lower + EPS)
    bandwidth = (upper - lower) / (mid.abs() + EPS)
    return mid, upper, lower, pct_b, bandwidth


def donchian(high: pd.DataFrame, low: pd.DataFrame, n: int = C.DONCHIAN_PERIOD):
    return high.rolling(n, min_periods=n // 2).max(), low.rolling(n, min_periods=n // 2).min()


# --------------------------------------------------------------------------
# momentum
# --------------------------------------------------------------------------
def roc(close: pd.DataFrame, n: int) -> pd.DataFrame:
    return close / close.shift(n) - 1.0


def momentum_12_1(close: pd.DataFrame, lookback: int = 252, skip: int = 21) -> pd.DataFrame:
    """Classic 12-1 momentum: 12-month return excluding the most recent month.

    The skip is not cosmetic - short-term reversal is one of the most
    reliable effects in equities, so the last month's return is noise that
    contaminates the momentum signal.
    """
    return close.shift(skip) / close.shift(lookback) - 1.0


def up_day_ratio(close: pd.DataFrame, n: int = 63) -> pd.DataFrame:
    up = (close.diff() > 0).astype(float)
    up[close.isna() | close.shift(1).isna()] = np.nan
    return up.rolling(n, min_periods=n // 2).mean()


# --------------------------------------------------------------------------
# volume / flow
# --------------------------------------------------------------------------
def obv(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    sign = np.sign(close.diff()).fillna(0.0)
    return (sign * volume.fillna(0.0)).cumsum()


def obv_slope(close: pd.DataFrame, volume: pd.DataFrame, n: int = C.OBV_SLOPE_WINDOW) -> pd.DataFrame:
    """Net accumulation over n days expressed in average-daily-volume units."""
    o = obv(close, volume)
    avg_vol = volume.rolling(63, min_periods=20).mean()
    return (o - o.shift(n)) / (n * (avg_vol + EPS))


def ad_line(high, low, close, volume) -> pd.DataFrame:
    rng = (high - low)
    mfm = ((close - low) - (high - close)) / (rng + EPS)
    mfm = mfm.where(rng > EPS, 0.0)
    return (mfm * volume.fillna(0.0)).cumsum()


def cmf(high, low, close, volume, n: int = C.CMF_PERIOD) -> pd.DataFrame:
    """Chaikin Money Flow - volume-weighted close location within the bar range."""
    rng = (high - low)
    mfm = ((close - low) - (high - close)) / (rng + EPS)
    mfm = mfm.where(rng > EPS, 0.0)
    mfv = (mfm * volume).rolling(n, min_periods=n // 2).sum()
    vf = volume.rolling(n, min_periods=n // 2).sum()
    return mfv / (vf + EPS)


def mfi(high, low, close, volume, n: int = C.MFI_PERIOD) -> pd.DataFrame:
    tp = (high + low + close) / 3.0
    rmf = tp * volume
    d = tp.diff()
    pos = rmf.where(d > 0, 0.0).rolling(n, min_periods=n // 2).sum()
    neg = rmf.where(d < 0, 0.0).rolling(n, min_periods=n // 2).sum()
    return 100.0 - 100.0 / (1.0 + pos / (neg + EPS))


def rolling_vwap(high, low, close, volume, n: int = 20) -> pd.DataFrame:
    tp = (high + low + close) / 3.0
    pv = (tp * volume).rolling(n, min_periods=n // 2).sum()
    v = volume.rolling(n, min_periods=n // 2).sum()
    return pv / (v + EPS)


def dollar_volume(close: pd.DataFrame, volume: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return (close * volume).rolling(n, min_periods=max(3, n // 2)).mean()


def rvol(volume: pd.DataFrame, short: int = C.VOL_LOOKBACK_SHORT, long: int = C.VOL_LOOKBACK_LONG):
    vs = volume.rolling(short, min_periods=2).mean()
    vl = volume.rolling(long, min_periods=20).mean()
    return vs / (vl + EPS)


# --------------------------------------------------------------------------
# position / stretch
# --------------------------------------------------------------------------
def zscore(close: pd.DataFrame, n: int) -> pd.DataFrame:
    m = close.rolling(n, min_periods=max(5, n // 2)).mean()
    s = close.rolling(n, min_periods=max(5, n // 2)).std()
    return (close - m) / (s + EPS)


def atr_stretch(close: pd.DataFrame, ma: pd.DataFrame, atr_: pd.DataFrame) -> pd.DataFrame:
    """Distance from a moving average measured in ATR units (regime-free)."""
    return (close - ma) / (atr_ + EPS)


def range_position(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame,
                   n: int = 252) -> pd.DataFrame:
    """Percentile position within the trailing n-day high/low range, 0..1."""
    hh = high.rolling(n, min_periods=60).max()
    ll = low.rolling(n, min_periods=60).min()
    return (close - ll) / (hh - ll + EPS)


def rolling_percentile(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling percentile rank of the latest value within its own history."""
    return df.rolling(n, min_periods=max(20, n // 2)).rank(pct=True)


def slope_pct(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Normalised n-day slope: total change divided by mean level.

    Using (x_t - x_{t-n}) / mean(|x|) instead of a raw OLS slope keeps the
    measure comparable across tickers with wildly different price levels.
    """
    level = df.abs().rolling(n, min_periods=n // 2).mean()
    return (df - df.shift(n)) / (level + EPS)
