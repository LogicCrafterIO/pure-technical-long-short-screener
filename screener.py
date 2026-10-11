"""
Screen orchestrator.

Ties the four stages together:

    universe  ->  prices  ->  features  ->  scores/ranks/flags

and persists the result to ``output/`` as a dated CSV plus a ``latest``
pointer, so the dashboard starts instantly and you keep a dated history of
every screen you have run.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

import config as C
import data as D
import factors as F
import universe as U

LATEST_CSV = os.path.join(C.OUTPUT_DIR, "latest_screen.csv")
LATEST_META = os.path.join(C.OUTPUT_DIR, "latest_meta.json")


def run_screen(
    labels=None,
    force_universe: bool = False,
    force_prices: bool = False,
    verbose: bool = True,
    progress=None,
) -> tuple[pd.DataFrame, dict]:
    """Execute the full screen and return (results, meta)."""

    def say(msg):
        if verbose:
            print(msg)
        if progress:
            progress(msg)

    t0 = time.time()
    say("1/4  building universe ...")
    uni = U.load_universe(force=force_universe, verbose=verbose)
    if labels:
        uni = uni[uni["universes"].apply(
            lambda s: any(l in str(s).split("|") for l in labels)
        )].reset_index(drop=True)
    say(f"      {len(uni)} tickers in scope")

    say("2/4  loading price history ...")
    tickers = uni["ticker"].tolist()
    prices = D.download_prices(
        tickers,
        force=force_prices,
        verbose=verbose,
        progress=(lambda i, n, m: progress(m)) if progress else None,
    )
    say(f"      price matrix {prices['close'].shape[0]} days x "
        f"{prices['close'].shape[1]} tickers")

    say("3/4  computing technical features ...")
    # Features are computed on index members only; the full matrix (including
    # SPY/IWM/QQQ/TLT) is kept for the regime read below.
    px_uni = {f: prices[f].reindex(columns=tickers) for f in D.FIELDS}
    feats = F.compute_features(px_uni, verbose=verbose)

    say("4/4  scoring, ranking and flagging ...")
    scored = F.score_universe(feats, uni, verbose=verbose)
    regime = F.market_regime(prices, scored)
    scored = F.apply_regime_tilt(scored, regime)

    # tidy presentation columns
    round_map = {
        "chg_1d_pct": 2, "ret_1m": 2, "ret_3m": 2, "ret_6m": 2, "ret_12m": 2,
        "ret_12_1": 2, "rs_ret_1m": 2, "rs_ret_3m": 2, "rs_ret_6m": 2,
        "rsi14": 1, "rsi2": 1, "adx14": 1, "atr_pct": 2, "vol_21": 1, "vol_63": 1,
        "dist_sma20_pct": 2, "dist_sma50_pct": 2, "dist_sma200_pct": 2,
        "long_score": 3, "short_score": 3, "long_pct": 1, "short_pct": 1,
        "long_score_adj": 3, "short_score_adj": 3,
        "cmf_21": 3, "mfi14": 1, "rvol": 2, "obv_slope_63": 3,
        "pct_to_52w_high": 2, "pct_above_52w_low": 2, "range_pos_52w": 1,
        "stretch_atr_20": 2, "stretch_atr_50": 2, "stretch_atr_200": 2,
        "risk_pct": 2, "vwap20_dist_pct": 2, "bbw_pctile": 3, "vol_pctile": 3,
    }
    scored = scored.copy()
    for c, nd in round_map.items():
        if c in scored.columns:
            scored[c] = scored[c].round(nd)
    for c in ("entry", "stop_long", "target_long", "trail_long",
              "stop_short", "target_short", "trail_short", "close", "atr14"):
        if c in scored.columns:
            scored[c] = scored[c].round(4)

    scored["side"] = np.where(
        scored["rank_long"].fillna(1e9) <= scored["rank_short"].fillna(1e9),
        "LONG", "SHORT",
    )
    scored = scored.sort_values(
        ["eligible", "long_score"], ascending=[False, False]
    ).reset_index(drop=True)

    meta = {
        "run_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "as_of": str(prices["close"].index.max().date()),
        "n_universe": int(len(uni)),
        "n_eligible": int(scored["eligible"].sum()),
        "elapsed_sec": round(time.time() - t0, 1),
        "regime": regime,
        "universes": {k: int(v) for k, v in uni["universe"].value_counts().items()},
        "price_cache_age_days": D.price_cache_age_days(),
    }
    save_screen(scored, meta)
    say(f"      done in {meta['elapsed_sec']}s   "
        f"eligible={meta['n_eligible']}   regime={regime.get('regime')}")
    return scored, meta


def save_screen(scored: pd.DataFrame, meta: dict) -> None:
    out = scored.copy()
    for c in out.columns:
        if out[c].dtype == bool:
            out[c] = out[c].astype(int)
    keep = [c for c in out.columns if not c.startswith("_")]
    out = out[keep]
    out.to_csv(LATEST_CSV, index=False)
    dated = os.path.join(C.OUTPUT_DIR, f"screen_{meta.get('as_of', 'na')}.csv")
    out.to_csv(dated, index=False)
    with open(LATEST_META, "w") as fh:
        json.dump(meta, fh, indent=2, default=str)


def load_latest_screen() -> tuple[pd.DataFrame, dict]:
    """Load the most recent saved screen; (empty, {}) if none exists."""
    if not os.path.exists(LATEST_CSV):
        return pd.DataFrame(), {}
    df = pd.read_csv(LATEST_CSV, dtype={"ticker": str})
    meta = {}
    if os.path.exists(LATEST_META):
        try:
            with open(LATEST_META) as fh:
                meta = json.load(fh)
        except Exception:
            meta = {}
    # bool columns were int-encoded for CSV
    flag_cols = [c for c in df.columns if c.startswith("flag_")]
    restore = flag_cols + ["eligible", "is_optionable",
                           "above_sma20", "above_sma50", "above_sma200"]
    for c in restore:
        if c in df.columns:
            df[c] = df[c].fillna(0).astype(bool)
    return df, meta


def flag_columns(df: pd.DataFrame) -> list:
    return sorted([c for c in df.columns if c.startswith("flag_")])


if __name__ == "__main__":
    import sys

    res, meta = run_screen(
        force_universe="--force-universe" in sys.argv,
        force_prices="--force-prices" in sys.argv,
    )
    pd.set_option("display.width", 250)
    L = ["rank_long", "ticker", "sector", "close", "long_score",
         "ret_6m", "ret_12_1", "rsi14", "adx14", "atr_pct", "flags"]
    S = ["rank_short", "ticker", "sector", "close", "short_score",
         "ret_6m", "ret_12_1", "rsi14", "adx14", "atr_pct", "flags"]
    print("\n================ TOP 20 LONGS ================")
    print(res.nlargest(20, "long_score")[[c for c in L if c in res]].to_string(index=False))
    print("\n================ TOP 20 SHORTS ================")
    print(res.nlargest(20, "short_score")[[c for c in S if c in res]].to_string(index=False))
    print(f"\nregime={meta['regime'].get('regime')}  "
          f"breadth={meta['regime'].get('breadth_pct')}%\n"
          f"saved -> {LATEST_CSV}")
