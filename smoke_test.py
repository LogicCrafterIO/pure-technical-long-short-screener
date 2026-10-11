"""
Smoke test for the screener engine.

Runs the full feature/scoring pipeline on a small basket of tickers using a
direct yfinance pull (it does NOT touch the main price cache), so the maths can
be validated in ~30 seconds instead of waiting for a full 2,900-ticker refresh.

    python smoke_test.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import data as D
import factors as F
import indicators as I

BASKET = [
    # mega-cap uptrends
    "AAPL", "MSFT", "NVDA", "LLY", "AVGO",
    # laggards / downtrends
    "INTC", "PFE", "VZ", "WBA", "PARA",
    # high-beta small caps
    "PLUG", "RIVN", "SOFI", "IONQ", "AFRM",
    # mid caps
    "CZR", "OKTA", "TWLO", "ETSY", "ZBRA",
    # benchmarks
    "SPY", "IWM",
]


def main() -> int:
    print("=" * 78)
    print("SMOKE TEST")
    print("=" * 78)

    print("\n[1] indicator sanity checks (synthetic series)")
    idx = pd.date_range("2022-01-03", periods=400, freq="B")
    rng = np.random.default_rng(7)
    px = pd.DataFrame(
        {"UP": 100 * np.exp(np.cumsum(rng.normal(0.0012, 0.010, len(idx)))),
         "DOWN": 100 * np.exp(np.cumsum(rng.normal(-0.0012, 0.010, len(idx))))},
        index=idx,
    )
    hi = px * 1.004
    lo = px * 0.996
    vol = pd.DataFrame(rng.integers(1e6, 5e6, px.shape), index=idx, columns=px.columns).astype(float)

    a = I.atr(hi, lo, px, 14)
    r = I.rsi(px, 14)
    adx_, pdi, mdi = I.adx(hi, lo, px, 14)
    assert a.iloc[-1].notna().all(), "ATR produced NaN"
    assert a.iloc[-1].min() > 0, "ATR must be positive"
    assert r.iloc[-1].between(0, 100).all(), f"RSI out of range: {r.iloc[-1].values}"
    assert adx_.iloc[-1].between(0, 100).all(), "ADX out of range"
    assert (r["UP"] > r["DOWN"]).iloc[-1], "uptrend RSI should exceed downtrend RSI"
    print(f"    ATR last = {a.iloc[-1].round(3).to_dict()}")
    print(f"    RSI last = {r.iloc[-1].round(1).to_dict()}")
    print(f"    ADX last = {adx_.iloc[-1].round(1).to_dict()}  "
          f"(+DI {pdi.iloc[-1].round(1).to_dict()}, -DI {mdi.iloc[-1].round(1).to_dict()})")

    # deterministic trend: ADX must be high and RSI pinned in the trend direction
    trend = pd.DataFrame(
        {"TUP": 100 * 1.005 ** np.arange(len(idx)),
         "TDN": 100 * 0.995 ** np.arange(len(idx))}, index=idx)
    adx_t, pdi_t, mdi_t = I.adx(trend * 1.002, trend * 0.998, trend, 14)
    rsi_t = I.rsi(trend, 14)
    assert adx_t.iloc[-1]["TUP"] > 60, f"clean trend ADX too low: {adx_t.iloc[-1]['TUP']}"
    assert pdi_t.iloc[-1]["TUP"] > mdi_t.iloc[-1]["TUP"], "+DI must dominate in an uptrend"
    assert pdi_t.iloc[-1]["TDN"] < mdi_t.iloc[-1]["TDN"], "-DI must dominate in a downtrend"
    assert rsi_t.iloc[-1]["TUP"] > 95 and rsi_t.iloc[-1]["TDN"] < 5, "RSI pinned wrong"
    print(f"    clean-trend check: ADX={adx_t.iloc[-1]['TUP']:.0f}, "
          f"RSI up={rsi_t.iloc[-1]['TUP']:.0f} / down={rsi_t.iloc[-1]['TDN']:.0f}  OK")

    print("\n[2] gaussian rank transform")
    s = pd.Series(np.r_[np.linspace(-5, 5, 200), 1000.0])  # includes a huge outlier
    gr = F.cs_gauss_rank(s)
    assert gr.notna().all()
    assert abs(gr.mean()) < 0.05, f"gauss rank not centred: {gr.mean()}"
    assert 0.7 < gr.std() < 1.4, f"gauss rank should be ~unit variance, got {gr.std()}"
    assert gr.max() <= C.WINSOR_Z + 1e-9
    assert gr.is_monotonic_increasing, "rank transform must be monotone"
    print(f"    mean={gr.mean():.4f} std={gr.std():.3f} min={gr.min():.2f} max={gr.max():.2f}")
    print(f"    the +1000 outlier maps to {gr.iloc[-1]:.2f} (capped), not to infinity")

    print(f"\n[3] downloading {len(BASKET)} tickers from yfinance ...")
    res = D._download_batch(BASKET, C.PRICE_PERIOD, verbose=True)
    assert res and "close" in res, "download failed"
    prices = {k: res.get(k, pd.DataFrame(index=res["close"].index)) for k in D.FIELDS}
    close = prices["close"]
    assert close.shape[1] >= len(BASKET) - 3, f"too few tickers returned: {close.shape}"
    print(f"    close matrix: {close.shape[0]} days x {close.shape[1]} tickers, "
          f"last {close.index[-1].date()}")

    print("\n[4] feature construction")
    feats = F.compute_features(prices, verbose=True)
    required = ["close", "atr14", "rsi14", "adx14", "sma50", "sma200", "ret_12_1",
                "mom6_over_vol", "obv_slope_63", "stretch_atr_20", "avg_dollar_vol_20",
                "bbw_pctile", "range_pos_52w", "chg_1d_pct"]
    missing = [c for c in required if c not in feats.columns]
    assert not missing, f"missing features: {missing}"
    print(f"    {feats.shape[1]} features built, none missing")

    meta = pd.DataFrame({
        "ticker": list(close.columns),
        "name": [f"{t} Inc" for t in close.columns],
        "sector": ["Test"] * len(close.columns),
        "universe": ["SP500"] * len(close.columns),
        "universes": ["SP500"] * len(close.columns),
    })

    print("\n[5] scoring")
    scored = F.score_universe(feats, meta, verbose=True)
    key = ["ticker", "close", "long_score", "short_score", "rank_long", "rank_short",
           "ret_6m", "rsi14", "adx14", "atr_pct", "flags"]
    out = scored[[c for c in key if c in scored.columns]].sort_values(
        "long_score", ascending=False)
    print("\n    ranked by long_score:")
    print(out.to_string(index=False))

    # invariants
    assert scored["long_score"].notna().sum() > 5, "almost nothing scored"
    assert scored.loc[scored["eligible"], "long_score"].notna().all(), \
        "eligible names must have a long score"
    assert scored["rank_long"].min() == 1, "rank_long must start at 1"
    assert scored["rank_short"].min() == 1, "rank_short must start at 1"
    corr = scored[["long_score", "short_score"]].corr().iloc[0, 1]
    assert -0.98 < corr < 0.3, f"long/short scores badly behaved: {corr:.2f}"
    print(f"\n    correlation(long_score, short_score) = {corr:.3f}")
    print("    (strongly negative but not -1: the short score carries its own")
    print("     weights plus squeeze and volatility penalties, so shorting the")
    print("     longs is NOT the same trade as the short book)")

    up = scored[scored["ticker"] == "NVDA"]
    dn = scored[scored["ticker"] == "INTC"]
    if len(up) and len(dn):
        print(f"    NVDA long_score={up['long_score'].iloc[0]:.2f}  "
              f"INTC long_score={dn['long_score'].iloc[0]:.2f}")
        print(f"    NVDA short_score={up['short_score'].iloc[0]:.2f}  "
              f"INTC short_score={dn['short_score'].iloc[0]:.2f}")

    print("\n[6] regime")
    reg = F.market_regime(prices, scored)
    print(f"    {reg}")

    print("\n" + "=" * 78)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
