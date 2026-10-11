"""
Visualisation dashboard for the screener CSV output.

Standalone: it reads `output/*.csv` (or a file you upload) and does nothing
else — no pipeline, no downloads. Built for filtering, sorting and visually
exploring the ranked rows.

    streamlit run dashboard.py
    python run_dashboard.py --mode csv      # convenience launcher

Filtering, in the order it appears in the sidebar:
    Book (long / short / both)  ->  Optionable  ->  Price  ->  everything else

Sorting is explicit (column + direction + optional secondary column) and orders
the whole filtered set; clicking a header re-sorts the page on screen. Every
table column can be shown or hidden, and every row carries a 📋 button that
copies that ticker's research prompt.
"""

from __future__ import annotations

import glob
import math
import os
import sys

import numpy as np
import pandas as pd
import streamlit as st

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

import config as C  # noqa: E402
from prompt_ui import PROMPT_STYLES, copy_table, prompt_panel  # noqa: E402
from prompt_ui import prompts_frame, rank_first  # noqa: E402
from universe import normalise_sector  # noqa: E402

try:
    import plotly.express as px
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAVE_PLOTLY = True
except Exception:  # pragma: no cover
    HAVE_PLOTLY = False

st.set_page_config(
    page_title="Screener Explorer",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

INDEXES = ["SP500", "SP400", "SP600", "RUSSELL2000"]
INDEX_LABEL = {
    "SP500": "S&P 500", "SP400": "S&P 400",
    "SP600": "S&P 600", "RUSSELL2000": "Russell 2000",
}

COLUMN_SETS = {
    "Core (recommended)": [
        "rank", "ticker", "name", "sector", "universe", "side", "is_optionable",
        "rank_long", "rank_short",
        "score", "close", "chg_1d_pct", "rsi14", "adx14",
        "ret_3m", "ret_6m", "ret_12_1", "atr_pct", "avg_dollar_vol_m", "flags",
    ],
    "Identity + scores": [
        "rank", "ticker", "name", "sector", "universe", "universes", "side",
        "is_optionable", "long_score", "short_score", "long_pct", "short_pct",
        "rank_long", "rank_short",
    ],
    "Momentum": [
        "rank", "ticker", "side", "close", "ret_1m", "ret_3m", "ret_6m",
        "ret_12m", "ret_12_1", "rs_ret_1m", "rs_ret_3m", "rs_ret_6m",
        "mom6_over_vol", "up_day_ratio_63", "vol_63",
    ],
    "Trend + strength": [
        "rank", "ticker", "side", "close", "sma20", "sma50", "sma200",
        "dist_sma20_pct", "dist_sma50_pct", "dist_sma200_pct", "sma50_slope",
        "sma200_slope", "adx14", "plus_di", "minus_di", "di_spread", "ma_stack",
    ],
    "Timing / mean reversion": [
        "rank", "ticker", "side", "close", "rsi14", "rsi2", "stoch_k",
        "williams_r", "pct_b", "zscore_20", "stretch_atr_20", "stretch_atr_50",
        "range_pos_52w", "pct_to_52w_high", "pct_above_52w_low",
    ],
    "Volume / flow": [
        "rank", "ticker", "side", "close", "avg_dollar_vol_m",
        "avg_dollar_vol_20", "rvol", "obv_slope_63", "cmf_21", "mfi14",
        "ad_line_slope", "vwap20_dist_pct",
    ],
    "Risk + trade levels": [
        "rank", "ticker", "side", "close", "atr14", "atr_pct", "risk_pct",
        "stop", "target", "reward_risk", "shares_for_1pct_risk",
        "vol_21", "vol_63", "vol_pctile", "bbw_pctile",
    ],
    "Factor legs": [
        "rank", "ticker", "side", "close", "f_trend_long", "f_momentum_long",
        "f_riskadj_long", "f_relstrength_long", "f_volume_long", "f_entry_long",
        "f_trend_short", "f_momentum_short", "f_riskadj_short",
        "f_relstrength_short", "f_volume_short", "f_entry_short",
        "f_squeeze_penalty",
    ],
    "Flags only": None,   # filled in dynamically with every flag_* column
    "Everything": None,   # filled in dynamically
}

NUMERIC_EXCLUDE = {"ticker", "name", "sector", "universe", "universes", "flags",
                   "last_date"}

FLAG_HELP = {
    "uptrend": "Close > SMA50 > SMA200",
    "downtrend": "Close < SMA50 < SMA200",
    "above_200sma": "Close above the 200-day SMA",
    "golden_cross": "SMA50 crossed above SMA200 within 20 sessions",
    "death_cross": "SMA50 crossed below SMA200 within 20 sessions",
    "macd_bull_cross": "MACD crossed above its signal within 10 sessions",
    "macd_bear_cross": "MACD crossed below its signal within 10 sessions",
    "rsi_oversold": "RSI(14) < 30",
    "rsi_overbought": "RSI(14) > 70",
    "rsi2_extreme": "RSI(2) < 5 or > 95 — short-term exhaustion",
    "bb_squeeze": "Bollinger bandwidth in the bottom 20% of 126 days",
    "volume_spike": "5-day volume ≥ 2× the 60-day average",
    "near_52w_high": "Within 2% of the 52-week high",
    "near_52w_low": "Within 2% of the 52-week low",
    "new_high_252": "New 252-day closing high",
    "new_low_252": "New 252-day closing low",
    "breakout_20d": "Close at/above the prior 20-day Donchian high",
    "breakdown_20d": "Close at/below the prior 20-day Donchian low",
    "gap_up_today": "Opened > 3% above the previous close",
}

PRESETS = {
    "— none —": {},
    "Momentum longs (uptrend, strong trend)": {
        "f_book": "Longs", "f_inc_flags": ["flag_uptrend"],
        "f_flag_mode": "ALL (AND)", "f_min_adx": 20.0, "f_rsi": (30.0, 78.0),
    },
    "Pullback longs (uptrend, RSI 40-60)": {
        "f_book": "Longs", "f_inc_flags": ["flag_uptrend"],
        "f_flag_mode": "ALL (AND)", "f_rsi": (40.0, 60.0), "f_min_adx": 15.0,
    },
    "Breakout longs (20d high, tight squeeze)": {
        "f_book": "Longs", "f_inc_flags": ["flag_breakout_20d"],
        "f_flag_mode": "ALL (AND)",
    },
    "Squeeze watchlist (coiled range)": {
        "f_inc_flags": ["flag_bb_squeeze"], "f_flag_mode": "ALL (AND)",
        "f_max_atr": 8.0,
    },
    "Volume spikes": {
        "f_inc_flags": ["flag_volume_spike"], "f_flag_mode": "ALL (AND)",
    },
    "Downtrend shorts (strong trend)": {
        "f_book": "Shorts", "f_inc_flags": ["flag_downtrend"],
        "f_flag_mode": "ALL (AND)", "f_min_adx": 20.0,
    },
    "Capitulation shorts (oversold, near lows)": {
        "f_book": "Shorts", "f_inc_flags": ["flag_rsi_oversold", "flag_near_52w_low"],
        "f_flag_mode": "ANY (OR)",
    },
    "Fresh crosses only": {
        "f_inc_flags": ["flag_golden_cross", "flag_death_cross",
                        "flag_macd_bull_cross", "flag_macd_bear_cross"],
        "f_flag_mode": "ANY (OR)",
    },
    "Liquid optionable large caps": {
        "f_indices": ["SP500"], "f_optionable": "Optionable only",
        "f_min_dv": 25.0,
    },
}

PAGE_SIZES = [10, 25, 50, 100, 200, 500]

# The copy-button table embeds every prompt it shows, so it is capped: a full
# brief is ~7.5 KB, and the component is re-sent on each rerun.
COPY_TABLE_MAX = 100

# Raw indicator levels, as opposed to scores/percentiles.  Handy to hide when
# you only care about the ranked view — they are the widest columns.
LEVEL_COLS = {
    "sma20", "sma50", "sma200", "ema21", "atr14", "prev_close", "open",
    "high", "low", "hi_52w", "lo_52w", "entry", "rolling_vwap20",
    "stop_long", "stop_short", "target_long", "target_short",
    "trail_long", "trail_short", "stop", "target",
}


def preset_columns(colset: str, df: pd.DataFrame) -> list:
    """Columns that a named column set selects (existing columns only)."""
    if colset == "Flags only":
        base = (["ticker", "name", "side", "close", "flags", "n_flags"] +
                [c for c in df.columns if c.startswith("flag_")])
    elif colset == "Everything":
        base = list(df.columns)
    else:
        base = list(COLUMN_SETS.get(colset) or [])
    out = [c for c in base if c in df.columns]
    if colset != "Everything":
        for c in ("side", "is_optionable"):
            if c in df.columns and c not in out:
                out.append(c)
    return list(dict.fromkeys(out))


DEFAULTS = {
    "f_book": "Both",
    "f_optionable": "All",
    "f_price_on": True,
    "f_price": (5.0, 500.0),
    "f_indices": INDEXES,
    "f_sectors": [],
    "f_min_score": -10.0,
    "f_rank_cut": 0,
    "f_regime": False,
    "f_eligible": True,
    "f_min_dv": 0.0,
    "f_max_atr": 60.0,
    "f_min_adx": 0.0,
    "f_rsi": (0.0, 100.0),
    "f_rangepos": (0.0, 100.0),
    "f_inc_flags": [],
    "f_flag_mode": "ALL (AND)",
    "f_exc_flags": [],
    "f_search": "",
    "sort_col": "— default (score) —",
    "sort_asc": "Descending",
    "sort_col2": "— none —",
    "colset": "Core (recommended)",
    "f_cols": None,       # None => (re)load from the selected column set
    "page_size": 50,
    "page_no": 1,
    # research prompts
    "prompt_style": "full",
    "res_batch_n": 25,
    "res_batch_style": "full",
}


# ==========================================================================
# data loading
# ==========================================================================
def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise the CSV into the shape the dashboard expects."""
    df = df.copy()

    # collapse source-specific sector spellings (e.g. "Communication" from
    # iShares vs "Communication Services" from Wikipedia) so the facet is clean
    # even when reading a CSV written before the label was normalised upstream
    if "sector" in df.columns:
        df["sector"] = df["sector"].map(normalise_sector)

    for c in df.columns:
        if c.startswith("flag_") or c in ("eligible", "is_optionable",
                                          "above_sma20", "above_sma50",
                                          "above_sma200"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(bool)

    for ix in INDEXES:
        col = f"in_{ix}"
        if "universes" in df.columns:
            df[col] = df["universes"].fillna("").str.contains(ix, regex=False)
        else:
            df[col] = df.get("universe", "").eq(ix)

    # regime-adjusted metrics as first-class columns
    for a, b in (("long_score_adj", "long_score"),
                 ("short_score_adj", "short_score"),
                 ("rank_long_adj", "rank_long"),
                 ("rank_short_adj", "rank_short")):
        if a not in df.columns and b in df.columns:
            df[a] = df[b]

    # a single "display score" that depends on which book is being viewed
    df["long_disp"] = np.where(df["side"].eq("LONG"), df["long_score"],
                               df["short_score"])
    df["stop"] = np.where(df["side"].eq("LONG"), df.get("stop_long", np.nan),
                          df.get("stop_short", np.nan))
    df["target"] = np.where(df["side"].eq("LONG"), df.get("target_long", np.nan),
                            df.get("target_short", np.nan))
    df["score"] = np.where(df["side"].eq("LONG"), df["long_score"],
                           df["short_score"])
    return df


@st.cache_data(show_spinner=False)
def load_csv(path: str, mtime: float) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype={"ticker": str})
    return _prepare(raw)


@st.cache_data(show_spinner=False)
def load_upload(data: bytes) -> pd.DataFrame:
    import io
    return _prepare(pd.read_csv(io.BytesIO(data), dtype={"ticker": str}))


def available_csvs() -> list:
    out = []
    for p in sorted(glob.glob(os.path.join(C.OUTPUT_DIR, "*.csv"))):
        out.append(p)
    # newest first, but keep latest_screen.csv at the very top
    out.sort(key=lambda p: ("latest_screen" not in p, -os.path.getmtime(p)))
    return out


# ==========================================================================
# research prompts — the hand-off from "what the tape is doing" to "why"
# ==========================================================================
# `copy_table` (the ranked grid, with a 📋 button on every row), `prompt_panel`
# and `PROMPT_STYLES` come from prompt_ui.py so that this dashboard and the
# pipeline one present the prompt identically.  The prompt text itself is built
# by research_prompt.py (no Streamlit in it).


# ==========================================================================
# sidebar — filters
# ==========================================================================
def set_state(**kw) -> None:
    for k, v in kw.items():
        st.session_state[k] = v


# Seed every filter control from DEFAULTS before any widget is instantiated.
# Widgets below are declared with `key=` only, so Streamlit reads their value
# from session_state.  This is also what lets the preset buttons and "Reset all"
# work without ever writing to a key after its widget exists.
for _k, _v in DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


with st.sidebar:
    st.markdown("## 🔬 Screener Explorer")

    # ---- data source --------------------------------------------------
    with st.expander("📂 Data source", expanded=False):
        csvs = available_csvs()
        if csvs:
            labels = [os.path.basename(p) for p in csvs]
            pick = st.selectbox("Screen file", labels, index=0)
            path = csvs[labels.index(pick)]
            mt = os.path.getmtime(path)
            st.caption(f"{os.path.getsize(path)/1e6:.1f} MB · "
                       f"modified {pd.Timestamp(mt).strftime('%d %b %H:%M')}")
        else:
            path, mt = None, 0.0
            st.warning("No CSV found in output/. Run the screener or upload one.")
        up = st.file_uploader("…or upload a CSV", type=["csv"])

    if up is not None:
        df_all = load_upload(up.getvalue())
        src = f"uploaded: {up.name}"
    elif path:
        df_all = load_csv(path, mt)
        src = os.path.basename(path)
    else:
        st.error("Nothing to display.")
        st.stop()

    # ---- presets -------------------------------------------------------
    st.markdown("### ⚡ Presets")
    preset = st.selectbox("Quick filter", list(PRESETS.keys()), index=0,
                          label_visibility="collapsed")
    b1, b2 = st.columns(2)
    if b1.button("Apply", use_container_width=True, type="primary"):
        set_state(**DEFAULTS)
        set_state(**PRESETS[preset])
        st.rerun()
    if b2.button("Reset all", use_container_width=True):
        for k, v in DEFAULTS.items():
            st.session_state[k] = v
        st.rerun()

    st.divider()

    # ==================================================================
    # 1. BOOK  (long / short)
    # ==================================================================
    st.markdown("### 1️⃣ Book")
    book = st.radio("Side", ["Longs", "Shorts", "Both"], key="f_book",
                    horizontal=True, label_visibility="collapsed")
    st.caption("Longs / Shorts are the screener's own classification: a name is "
               "a long if its long composite beats its short composite, and vice "
               "versa. Use the rank cutoff below to keep only the top N of "
               "that book.")
    st.checkbox("Use regime-adjusted score", key="f_regime",
                help="Applies the risk-on/risk-off tilt baked into "
                     "long_score_adj / short_score_adj.")
    st.slider("Rank cutoff per book (0 = off)", 0, 1000, key="f_rank_cut", step=10,
              help="Keep only the top N names of the selected book(s).")
    st.slider("Minimum score", -10.0, 3.0, key="f_min_score", step=0.05,
              help="Composite score floor, in cross-sectional σ units.")

    st.divider()

    # ==================================================================
    # 2. OPTIONABLE
    # ==================================================================
    st.markdown("### 2️⃣ Optionable")
    st.radio("Optionable filter",
             ["All", "Optionable only", "Non-optionable only"],
             key="f_optionable", label_visibility="collapsed")
    st.caption("Proxy for listed options: price ≥ $5 and 20-day dollar volume "
               "≥ $5M. Also the practical gate for shortability.")

    st.divider()

    # ==================================================================
    # 3. PRICE
    # ==================================================================
    st.markdown("### 3️⃣ Price")
    px_all = df_all["close"].dropna()
    lo_bound = float(max(0.0, np.floor(px_all.min()))) if len(px_all) else 0.0
    hi_cap = float(np.ceil(px_all.quantile(0.999))) if len(px_all) else 1000.0
    hi_bound = float(max(hi_cap, lo_bound + 1.0))
    step = max(0.5, round((hi_bound - lo_bound) / 400, 2))

    # clamp the stored range into the slider's legal bounds (safe: no widget
    # with this key exists yet in this run)
    _pa, _pb = st.session_state["f_price"]
    st.session_state["f_price"] = (float(np.clip(_pa, lo_bound, hi_bound)),
                                   float(np.clip(_pb, lo_bound, hi_bound)))

    st.checkbox("Enable price filter", key="f_price_on")
    if st.session_state["f_price_on"]:
        bands = {
            "≤ $25": (lo_bound, 25.0),
            "$5–100": (5.0, 100.0),
            "$25–500": (25.0, 500.0),
            "≥ $200": (200.0, hi_bound),
        }
        bcols = st.columns(len(bands))
        for col, (lbl, (a, b)) in zip(bcols, bands.items()):
            if col.button(lbl, use_container_width=True, key=f"band_{lbl}"):
                st.session_state["f_price"] = (
                    float(np.clip(a, lo_bound, hi_bound)),
                    float(np.clip(max(b, a), lo_bound, hi_bound)))
                st.rerun()
        st.slider("Price range ($)", lo_bound, hi_bound, key="f_price", step=step)
        _lo, _hi = st.session_state["f_price"]
        n_in = int(df_all["close"].between(_lo, _hi, inclusive="both").sum())
        st.caption(f"${_lo:,.2f} – ${_hi:,.2f} keeps {n_in:,} of {len(df_all):,} rows. "
                   f"Full range ${lo_bound:,.0f} – ${hi_bound:,.0f}.")

    st.divider()

    # ==================================================================
    # 4. INDEX / SECTOR
    # ==================================================================
    st.markdown("### 4️⃣ Index & sector")
    st.multiselect("Index membership", INDEXES, key="f_indices",
                   format_func=lambda k: INDEX_LABEL.get(k, k),
                   help="A ticker in more than one index matches any of them.")
    sectors = sorted(df_all["sector"].dropna().unique())
    st.multiselect("Sector", sectors, key="f_sectors",
                   placeholder="all sectors")
    st.checkbox("Only names passing the liquidity gates", key="f_eligible")

    st.divider()

    # ==================================================================
    # 5. LIQUIDITY & RISK
    # ==================================================================
    st.markdown("### 5️⃣ Liquidity & risk")
    st.slider("Min 20-day $ volume ($M)", 0.0, 100.0, key="f_min_dv", step=0.5)
    st.slider("Max ATR % of price", 0.0, 60.0, key="f_max_atr", step=0.5)
    st.slider("Min ADX(14)  (trend strength)", 0.0, 60.0, key="f_min_adx", step=1.0)
    st.slider("RSI(14) range", 0.0, 100.0, key="f_rsi", step=1.0)
    st.slider("52-week range position (%)", 0.0, 100.0, key="f_rangepos", step=1.0,
              help="0 = at the 52-week low, 100 = at the high.")

    st.divider()

    # ==================================================================
    # 6. FLAGS
    # ==================================================================
    st.markdown("### 6️⃣ Optional flags")
    flag_cols = sorted([c for c in df_all.columns if c.startswith("flag_")])
    st.multiselect("Must have", flag_cols, key="f_inc_flags",
                   format_func=lambda f: f.replace("flag_", "").replace("_", " "),
                   placeholder="no requirement")
    st.radio("Match", ["ALL (AND)", "ANY (OR)"], key="f_flag_mode",
             horizontal=True, label_visibility="collapsed")
    st.multiselect("Must NOT have", flag_cols, key="f_exc_flags",
                   format_func=lambda f: f.replace("flag_", "").replace("_", " "),
                   placeholder="no exclusions")
    with st.expander("What each flag means"):
        for f in flag_cols:
            st.caption(f"**{f.replace('flag_','')}** — {FLAG_HELP.get(f.replace('flag_',''),'')}")

    st.divider()

    # ==================================================================
    # 7. SEARCH + SORT + COLUMNS
    # ==================================================================
    st.markdown("### 7️⃣ Search & sort")
    st.text_input("Ticker or company contains", key="f_search",
                  placeholder="e.g. AAPL, energy, bio")

    numeric_cols = [c for c in df_all.columns
                    if c not in NUMERIC_EXCLUDE
                    and pd.api.types.is_numeric_dtype(df_all[c])]
    sort_options = (["— default (score) —"] +
                    ["rank_long", "rank_short", "long_score", "short_score",
                     "long_pct", "short_pct"] +
                    sorted(set(numeric_cols) - {"rank_long", "rank_short",
                                                "long_score", "short_score",
                                                "long_pct", "short_pct"}))
    st.selectbox("Sort by", sort_options, key="sort_col")
    st.radio("Direction", ["Descending", "Ascending"], key="sort_asc",
             horizontal=True, label_visibility="collapsed",
             format_func=lambda s: s)
    st.selectbox("Then by", ["— none —"] + sort_options[1:], key="sort_col2")

    st.divider()
    # ==================================================================
    # 8. COLUMN VISIBILITY
    # ==================================================================
    st.markdown("### 🧱 Columns")
    st.selectbox("Column set", list(COLUMN_SETS.keys()), key="colset",
                 help="A starting point. Edit the list below to add or remove "
                      "individual columns.")

    # `f_cols` is the single source of truth for what the table shows.  Picking
    # a column set reloads it; after that every column is individually toggleable
    # so the table can be narrowed down to just what you need.
    preset = preset_columns(st.session_state["colset"], df_all)
    all_columns = list(df_all.columns)
    if (st.session_state.get("f_cols") is None
            or st.session_state.get("_colset_sig") != st.session_state["colset"]):
        st.session_state["f_cols"] = list(preset)
        st.session_state["_colset_sig"] = st.session_state["colset"]

    def _set_cols(cols) -> None:
        st.session_state["f_cols"] = [c for c in cols if c in all_columns]

    def _drop_cols(pred) -> None:
        cur = st.session_state.get("f_cols") or []
        st.session_state["f_cols"] = [c for c in cur if not pred(c)]

    # NOTE: the label must stay static. Streamlit derives a widget's identity
    # from its label, so a label containing the live count would change on every
    # selection and silently reset the widget. The count goes in a caption below.
    st.multiselect("Columns to show",
                   all_columns, key="f_cols",
                   help="Remove a column to shrink the table; add any of the "
                        "others back. The displayed order is fixed, so the "
                        "table stays stable as you toggle.")
    st.caption(f"{len(st.session_state.get('f_cols') or []):,} of "
               f"{len(all_columns):,} columns selected")

    c1, c2, c3 = st.columns(3)
    c1.button("All", use_container_width=True, on_click=_set_cols,
              args=(all_columns,), help="show every column")
    c2.button("Preset", use_container_width=True, on_click=_set_cols,
              args=(list(preset),), help="back to the selected column set")
    c3.button("None", use_container_width=True, on_click=_set_cols, args=([],),
              help="hide everything, then add back what you want")
    d1, d2, d3 = st.columns(3)
    d1.button("− flags", use_container_width=True, on_click=_drop_cols,
              args=(lambda c: c.startswith("flag_"),),
              help="hide the flag_* columns")
    d2.button("− factors", use_container_width=True, on_click=_drop_cols,
              args=(lambda c: c.startswith("f_"),),
              help="hide the factor-leg columns")
    d3.button("− levels", use_container_width=True, on_click=_drop_cols,
              args=(lambda c: c in LEVEL_COLS,),
              help="hide raw indicator levels (SMA/ATR/stop/target/…)")
    st.caption(f"file: {src}")

    st.divider()
    # ==================================================================
    # 9. RESEARCH PROMPTS
    # ==================================================================
    st.markdown("### 💬 Research prompts")
    st.radio("Prompt style", list(PROMPT_STYLES), key="prompt_style",
             format_func=lambda k: PROMPT_STYLES[k],
             label_visibility="collapsed",
             help="What every 'copy prompt' button quotes.")
    st.caption("The screen is technical only. These prompts ask a chatbot for the "
               "**fundamental story** behind the move and whether it is likely to "
               "continue over the next 1-3 months. Grab one per ticker with the "
               "**📋 Copy** button on every row of **📋 Table**, or read the full text in "
               "the **💬 Research** / **🕯️ Ticker detail** tabs.")


# ==========================================================================
# resolve session_state booleans for sort direction
# ==========================================================================
_book = st.session_state["f_book"]
_regime = st.session_state["f_regime"]
_asc = st.session_state["sort_asc"] == "Ascending"
# what every "copy prompt" block quotes (set in the sidebar, section 9)
prompt_style = st.session_state.get("prompt_style", "full")

metric_long = "long_score_adj" if _regime else "long_score"
metric_short = "short_score_adj" if _regime else "short_score"
rank_long = "rank_long_adj" if _regime else "rank_long"
rank_short = "rank_short_adj" if _regime else "rank_short"


# ==========================================================================
# filtering
# ==========================================================================
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    d = df

    if st.session_state["f_eligible"]:
        d = d[d["eligible"]]

    # --- book: a genuine row filter on the screener's own classification ---
    if _book == "Longs":
        d = d[d["side"].eq("LONG") & d[metric_long].notna()]
    elif _book == "Shorts":
        d = d[d["side"].eq("SHORT") & d[metric_short].notna()]
    else:
        d = d[d[metric_long].notna() | d[metric_short].notna()]

    # --- score floor (applied to whichever book is in view) -----------
    ms = st.session_state["f_min_score"]
    if _book == "Longs":
        d = d[d[metric_long] >= ms]
    elif _book == "Shorts":
        d = d[d[metric_short] >= ms]
    else:
        d = d[(d[metric_long] >= ms) | (d[metric_short] >= ms)]

    # --- rank cutoff --------------------------------------------------
    cut = st.session_state["f_rank_cut"]
    if cut:
        if _book == "Longs":
            d = d[d[rank_long] <= cut]
        elif _book == "Shorts":
            d = d[d[rank_short] <= cut]
        else:
            d = d[(d[rank_long] <= cut) | (d[rank_short] <= cut)]

    # --- optionable ---------------------------------------------------
    opt = st.session_state["f_optionable"]
    if opt == "Optionable only":
        d = d[d["is_optionable"]]
    elif opt == "Non-optionable only":
        d = d[~d["is_optionable"]]

    # --- price --------------------------------------------------------
    if st.session_state["f_price_on"]:
        pmin, pmax = st.session_state["f_price"]
        d = d[d["close"].between(float(pmin), float(pmax), inclusive="both")]

    # --- index --------------------------------------------------------
    idx = st.session_state["f_indices"]
    if idx and len(idx) < len(INDEXES):
        d = d[d[[f"in_{i}" for i in idx]].any(axis=1)]

    # --- sector -------------------------------------------------------
    sec = st.session_state["f_sectors"]
    if sec:
        d = d[d["sector"].isin(sec)]

    # --- liquidity / risk ---------------------------------------------
    d = d[d["avg_dollar_vol_m"].fillna(0) >= st.session_state["f_min_dv"]]
    d = d[d["atr_pct"].fillna(1e9) <= st.session_state["f_max_atr"]]
    d = d[d["adx14"].fillna(0) >= st.session_state["f_min_adx"]]
    r0, r1 = st.session_state["f_rsi"]
    d = d[d["rsi14"].fillna(50).between(r0, r1)]
    q0, q1 = st.session_state["f_rangepos"]
    d = d[d["range_pos_52w"].fillna(50).between(q0, q1)]

    # --- flags --------------------------------------------------------
    inc = st.session_state["f_inc_flags"]
    if inc:
        m = d[inc]
        d = d[m.all(axis=1)] if st.session_state["f_flag_mode"].startswith("ALL") \
            else d[m.any(axis=1)]
    exc = st.session_state["f_exc_flags"]
    if exc:
        d = d[~d[exc].any(axis=1)]

    # --- search -------------------------------------------------------
    q = st.session_state["f_search"].strip()
    if q:
        blob = (d["ticker"].fillna("") + " " + d["name"].fillna("") + " " +
                d["sector"].fillna(""))
        d = d[blob.str.contains(q, case=False, regex=False, na=False)]

    return d


work = apply_filters(df_all)

# ==========================================================================
# sorting
# ==========================================================================
sort_col = st.session_state["sort_col"]
if sort_col in ("", "— default (score) —"):
    if _book == "Longs":
        sort_col, asc = metric_long, False
    elif _book == "Shorts":
        sort_col, asc = metric_short, False
    else:
        sort_col, asc = "score", False
else:
    asc = _asc

if sort_col not in work.columns:
    sort_col, asc = "score", False

by = [sort_col]
ascending = [asc]
second = st.session_state["sort_col2"]
if second not in ("— none —", "", sort_col) and second in work.columns:
    by.append(second)
    ascending.append(False)

work = work.sort_values(by, ascending=ascending, na_position="last")


# ==========================================================================
# column resolution — the sidebar picker (`f_cols`) is the source of truth
# ==========================================================================
flag_cols = sorted([c for c in df_all.columns if c.startswith("flag_")])
flegs = sorted([c for c in df_all.columns if c.startswith("f_")])

_selected = [c for c in (st.session_state.get("f_cols") or []) if c in work.columns]
# "view #" is the row number within the result set — deliberately not called
# "rank" so it cannot be mistaken for the model's rank_long / rank_short
# the copy buttons open the table, and the score sits next to the ticker
show_cols = rank_first(["view #"] + _selected, "score", "ticker")


# ==========================================================================
# pagination
# ==========================================================================
page_size = int(st.session_state["page_size"])
n_rows = len(work)
n_pages = max(1, int(math.ceil(n_rows / float(page_size))))

# Jump back to page 1 whenever the result set, its ordering, or the page size
# changes — otherwise the reader can land on an empty page.
_page_sig = (n_rows, sort_col, bool(asc), page_size)
if st.session_state.get("_page_sig") != _page_sig:
    st.session_state["_page_sig"] = _page_sig
    st.session_state["page_no"] = 1

page = int(np.clip(st.session_state.get("page_no", 1), 1, n_pages))
st.session_state["page_no"] = page          # safe: the widget is created later
start = (page - 1) * page_size
end = min(start + page_size, n_rows)

page_df = work.iloc[start:end].copy()
page_df.insert(0, "view #", np.arange(start + 1, end + 1))


def _goto(p) -> None:
    """on_click callback; runs before the next script pass, so it is always
    safe to write `page_no` here regardless of widget order."""
    st.session_state["page_no"] = int(p)


# ==========================================================================
# header
# ==========================================================================
st.title("🔬 Screener Explorer")
st.caption(f"**{src}** · {len(df_all):,} rows total · "
           f"**{len(work):,}** match the filters · "
           f"showing rows {start + 1:,}–{end:,} "
           f"(page {page} of {n_pages}) · "
           f"{len(show_cols)} columns")

k = st.columns(6)
k[0].metric("Rows after filters", f"{len(work):,}")
k[1].metric("Optionable", f"{int(work['is_optionable'].sum()):,}")
k[2].metric("Median price", f"${work['close'].median():,.2f}" if len(work) else "–")
k[3].metric("Median $vol (M)",
            f"{work['avg_dollar_vol_m'].median():,.1f}" if len(work) else "–")
k[4].metric("Median ATR%", f"{work['atr_pct'].median():.2f}%" if len(work) else "–")
k[5].metric("Longs / Shorts",
            f"{int((work['side']=='LONG').sum()):,} / {int((work['side']=='SHORT').sum()):,}"
            if len(work) else "–")

# active filter chips
chips = []
if _book != "Both":
    chips.append(f"book={_book}")
if st.session_state["f_optionable"] != "All":
    chips.append(st.session_state["f_optionable"])
if st.session_state["f_price_on"]:
    _plo, _phi = st.session_state["f_price"]
    chips.append(f"price ${_plo:,.0f}–${_phi:,.0f}")
if len(st.session_state["f_indices"]) < len(INDEXES):
    chips.append("index=" + ",".join(st.session_state["f_indices"]))
if st.session_state["f_sectors"]:
    chips.append(f"sectors={len(st.session_state['f_sectors'])}")
if st.session_state["f_min_dv"] > 0:
    chips.append(f"$vol≥{st.session_state['f_min_dv']}M")
if st.session_state["f_max_atr"] < 60:
    chips.append(f"ATR≤{st.session_state['f_max_atr']}%")
if st.session_state["f_min_adx"] > 0:
    chips.append(f"ADX≥{st.session_state['f_min_adx']}")
if st.session_state["f_inc_flags"]:
    chips.append("has: " + ",".join(f.replace("flag_", "")
                                    for f in st.session_state["f_inc_flags"]))
if st.session_state["f_exc_flags"]:
    chips.append("not: " + ",".join(f.replace("flag_", "")
                                    for f in st.session_state["f_exc_flags"]))
if st.session_state["f_search"]:
    chips.append(f'"{st.session_state["f_search"]}"')
if chips:
    st.info(" · ".join(chips))
else:
    st.success("No filters applied — showing the full ranked universe.")

if work.empty:
    st.warning("No rows match the current filters. Loosen a filter or hit **Reset all**.")
    st.stop()


# ==========================================================================
# tabs
# ==========================================================================
tab_table, tab_research, tab_charts, tab_compare, tab_detail = st.tabs(
    ["📋 Table", "💬 Research", "📊 Charts", "⚖️ Long vs Short", "🕯️ Ticker detail"]
)

# ---------------------------------------------------------------- TABLE
with tab_table:
    # ---- pagination controls ----------------------------------------
    p1, p2, p3, p4, p5, p6 = st.columns([1.5, 0.45, 0.45, 0.45, 0.45, 1.1])
    p1.selectbox("Rows per page", PAGE_SIZES, key="page_size")
    p2.button("⏮", use_container_width=True, on_click=_goto, args=(1,),
              disabled=page <= 1, help="first page")
    p3.button("◀", use_container_width=True, on_click=_goto, args=(page - 1,),
              disabled=page <= 1, help="previous page")
    p4.button("▶", use_container_width=True, on_click=_goto, args=(page + 1,),
              disabled=page >= n_pages, help="next page")
    p5.button("⏭", use_container_width=True, on_click=_goto, args=(n_pages,),
              disabled=page >= n_pages, help="last page")
    p6.number_input("Go to page", min_value=1, max_value=n_pages,
                    key="page_no", step=1,
                    help="Jump straight to a page number.")

    t1, t2, t3 = st.columns([1.5, 1, 1.3])
    t1.markdown(f"**Page {page}** of {n_pages:,} · rows {start + 1:,}–{end:,} "
                f"of {n_rows:,} · sorted by `{sort_col}` "
                f"({'asc' if asc else 'desc'})")
    t2.download_button("⬇ Download all filtered", 
                       work.to_csv(index=False).encode("utf-8"),
                       file_name=f"filtered_{n_rows}rows.csv", mime="text/csv",
                       use_container_width=True,
                       help="the whole filtered set, not just this page")
    t3.caption(f"{len(show_cols)} columns · {len(page_df)} rows on this page · sort "
               f"and columns are set in the sidebar")

    if len(show_cols) <= 1:
        st.warning("No columns selected. Pick some in the sidebar under "
                   "**🧱 Columns**.")
    else:
        n_shown = copy_table(page_df, cols=show_cols, style=prompt_style,
                             source=src, max_rows=COPY_TABLE_MAX,
                             height=min(900, 92 + 33 * len(page_df)),
                             hint="**📋 Copy** on any row puts that ticker's whole "
                                  "research prompt on the clipboard — one click, then "
                                  "paste it into ChatGPT / Claude / Gemini / Perplexity. "
                                  "Click a header to sort this page. To read a prompt in "
                                  "full, or keep it as a .txt file, use the **💬 "
                                  "Research** tab or **🕯️ Ticker detail**.")

    with st.expander("Filter summary"):
        s = st.columns(3)
        s[0].markdown("**By side**")
        s[0].dataframe(work["side"].value_counts().rename("count"),
                       use_container_width=True)
        s[1].markdown("**By index (primary)**")
        s[1].dataframe(work["universe"].value_counts().rename("count"),
                       use_container_width=True)
        s[2].markdown("**By optionable**")
        s[2].dataframe(work["is_optionable"].value_counts().rename("count"),
                       use_container_width=True)

# ---------------------------------------------------------------- RESEARCH
with tab_research:
    st.markdown("### 💬 Take one ticker to a chatbot")
    st.caption("This screen is **pure technical** — it can tell you what the tape is "
               "doing, never why. The prompt below is the bridge: it hands the "
               "ticker's own numbers to ChatGPT / Claude / Gemini / Perplexity and "
               "asks the two questions the screen cannot answer — **what was the "
               "fundamental story behind this share price move**, and **is it likely "
               "to continue over the next 1-3 months, and why**.")

    _tickers = work["ticker"].tolist()
    pick_res = st.selectbox(
        "Ticker to research", _tickers, index=0, key="res_ticker",
        help="Any name in the current filtered set — a search or a flag filter "
             "narrows this list.",
    )
    st.caption(f"{len(_tickers):,} name(s) in the filtered set · prompt style is set "
               f"in the sidebar under **💬 Research prompts**")
    if pick_res:
        prompt_panel(work[work["ticker"] == pick_res].iloc[0], key="res_prompt",
                     style=prompt_style, source=src)

    st.divider()
    with st.expander("📦 Batch — prompts for many tickers in one CSV", expanded=False):
        st.caption("One row per ticker with the prompt in the last column, so you can "
                   "work through a shortlist in a spreadsheet or paste them in "
                   "batches.")
        b1, b2 = st.columns([1, 2])
        b1.number_input("How many names (from the top of the view)", 1, 250,
                        step=5, key="res_batch_n")
        b2.radio("Batch style", list(PROMPT_STYLES), key="res_batch_style",
                 format_func=lambda k: PROMPT_STYLES[k], horizontal=True,
                 label_visibility="collapsed")
        _n = int(st.session_state["res_batch_n"])
        batch = prompts_frame(work, source=src, limit=_n,
                              style=st.session_state["res_batch_style"])
        st.download_button(f"⬇ Download {len(batch):,} prompt(s) as CSV",
                           batch.to_csv(index=False).encode("utf-8"),
                           file_name=f"research_prompts_{len(batch)}rows.csv",
                           mime="text/csv")
        st.dataframe(batch[["ticker", "name", "sector", "side", "score"]],
                     hide_index=True, use_container_width=True, height=220,
                     column_config={"score": st.column_config.NumberColumn(
                         "score", format="%.2f")})

# ---------------------------------------------------------------- CHARTS
with tab_charts:
    if not HAVE_PLOTLY:
        st.warning("Plotly not available — falling back to native charts.")
        st.bar_chart(work["sector"].value_counts().head(15))
    else:
        cA, cB = st.columns(2)
        with cA:
            st.markdown("**Score distribution by side**")
            fig = go.Figure()
            for side, colr in (("LONG", "#26a69a"), ("SHORT", "#ef5350")):
                sub = work[work["side"] == side]
                if len(sub):
                    fig.add_trace(go.Histogram(
                        x=sub["score"].dropna(), name=side, opacity=0.65,
                        marker_color=colr, nbinsx=40))
            fig.update_layout(barmode="overlay", height=340,
                              margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="composite score (σ)", yaxis_title="names",
                              legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

        with cB:
            st.markdown("**Candidates by sector**")
            vc = work["sector"].value_counts().head(18)[::-1]
            fig = go.Figure(go.Bar(x=vc.values, y=[str(i)[:28] for i in vc.index],
                                   orientation="h", marker_color="#4fc3f7"))
            fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="names")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.markdown("**Build your own scatter**")
        sc = st.columns(4)
        num = [c for c in work.columns
               if c not in NUMERIC_EXCLUDE and pd.api.types.is_numeric_dtype(work[c])]
        xcol = sc[0].selectbox("X axis", num, index=num.index("atr_pct")
                               if "atr_pct" in num else 0)
        ycol = sc[1].selectbox("Y axis", num, index=num.index("ret_6m")
                               if "ret_6m" in num else 1)
        ccol = sc[2].selectbox("Colour by", ["side", "sector", "is_optionable",
                                             "universe", "score", "atr_pct",
                                             "ret_6m"] , index=0)
        scol = sc[3].selectbox("Bubble size", ["— fixed —", "avg_dollar_vol_m",
                                               "atr_pct", "n_flags"], index=1)
        plot = work.dropna(subset=[xcol, ycol]).copy()
        if len(plot) > 3000:
            plot = plot.nlargest(3000, "score")
        size = None
        if scol != "— fixed —" and scol in plot.columns:
            size = np.clip(np.sqrt(plot[scol].fillna(0).abs()), 3, 40)
        fig = px.scatter(plot, x=xcol, y=ycol, color=ccol, hover_name="ticker",
                         hover_data=["sector", "close", "score", "rsi14", "adx14"],
                         size=size if size is not None else None,
                         color_continuous_scale="RdYlGn"
                         if ccol in ("score", "atr_pct", "ret_6m") else None)
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.divider()
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Risk vs reward: ATR% against 6-month return**")
            p2 = work.dropna(subset=["atr_pct", "ret_6m"])
            fig = px.scatter(p2, x="atr_pct", y="ret_6m", color="side",
                             hover_name="ticker", opacity=0.65,
                             color_discrete_map={"LONG": "#26a69a",
                                                 "SHORT": "#ef5350"})
            fig.add_hline(y=0, line_dash="dot", line_color="#888")
            fig.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="ATR % of price", yaxis_title="6m return %")
            st.plotly_chart(fig, use_container_width=True)
        with g2:
            st.markdown("**Price distribution (log scale)**")
            p3 = work[work["close"] > 0]
            fig = px.histogram(p3, x="close", color="side", nbins=50,
                               log_x=True, opacity=0.7,
                               color_discrete_map={"LONG": "#26a69a",
                                                   "SHORT": "#ef5350"})
            fig.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="price $ (log)", yaxis_title="names")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.markdown("**Factor profile of the top 20 overall**")
        if flegs:
            top = work.head(20)[["ticker"] + flegs].set_index("ticker")
            fig = go.Figure(go.Heatmap(
                z=top.values.T, x=[str(t) for t in top.index],
                y=[c.replace("f_", "").replace("_long", " (L)")
                   .replace("_short", " (S)") for c in flegs],
                colorscale="RdYlGn", zmid=0,
                colorbar=dict(title="σ")))
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.markdown("**Top 30 by the current sort**")
        top30 = work.head(30).iloc[::-1]
        fig = go.Figure(go.Bar(
            x=top30["score"], y=top30["ticker"], orientation="h",
            marker_color=np.where(top30["side"].eq("LONG"), "#26a69a", "#ef5350"),
            text=top30[sort_col].round(2), textposition="auto"))
        fig.update_layout(height=max(400, 20 * len(top30)),
                          margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title="composite score (σ)")
        st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- COMPARE
with tab_compare:
    st.markdown("### Long book vs short book")
    lg = work[work["side"] == "LONG"]
    sh = work[work["side"] == "SHORT"]
    comp = pd.DataFrame({
        "Long book": [
            f"{len(lg):,}",
            f"${lg['close'].median():,.2f}" if len(lg) else "–",
            f"{lg['atr_pct'].median():.2f}%" if len(lg) else "–",
            f"{lg['avg_dollar_vol_m'].median():,.1f}" if len(lg) else "–",
            f"{lg['rsi14'].median():.1f}" if len(lg) else "–",
            f"{lg['adx14'].median():.1f}" if len(lg) else "–",
            f"{lg['ret_6m'].median():.1f}%" if len(lg) else "–",
            f"{lg['ret_12_1'].median():.1f}%" if len(lg) else "–",
            f"{lg['vol_63'].median():.1f}%" if len(lg) else "–",
            f"{lg['is_optionable'].mean()*100:.0f}%" if len(lg) else "–",
        ],
        "Short book": [
            f"{len(sh):,}",
            f"${sh['close'].median():,.2f}" if len(sh) else "–",
            f"{sh['atr_pct'].median():.2f}%" if len(sh) else "–",
            f"{sh['avg_dollar_vol_m'].median():,.1f}" if len(sh) else "–",
            f"{sh['rsi14'].median():.1f}" if len(sh) else "–",
            f"{sh['adx14'].median():.1f}" if len(sh) else "–",
            f"{sh['ret_6m'].median():.1f}%" if len(sh) else "–",
            f"{sh['ret_12_1'].median():.1f}%" if len(sh) else "–",
            f"{sh['vol_63'].median():.1f}%" if len(sh) else "–",
            f"{sh['is_optionable'].mean()*100:.0f}%" if len(sh) else "–",
        ],
    }, index=["Names", "Median price", "Median ATR%", "Median $vol (M)",
              "Median RSI(14)", "Median ADX(14)", "Median 6m return",
              "Median 12-1 return", "Median 63d vol", "Optionable share"])
    st.dataframe(comp, use_container_width=True)
    st.caption("The two books are built from different weights plus short-side "
               "squeeze and volatility penalties, so differences here are by "
               "design rather than an artefact.")

    if HAVE_PLOTLY and flegs:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Mean factor profile by book**")
            lg_m = [lg[c].mean() for c in flegs if c.endswith("_long") or
                    c == "f_squeeze_penalty"]
            names = [c.replace("f_", "") for c in flegs if c.endswith("_long") or
                     c == "f_squeeze_penalty"]
            sh_m = [sh[c].mean() for c in flegs if c.endswith("_short") or
                    c == "f_squeeze_penalty"]
            fig = go.Figure()
            fig.add_bar(x=names, y=lg_m, name="Long book", marker_color="#26a69a")
            fig.add_bar(x=names, y=sh_m, name="Short book", marker_color="#ef5350")
            fig.update_layout(barmode="group", height=380,
                              margin=dict(l=10, r=10, t=10, b=10),
                              yaxis_title="mean σ")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            st.markdown("**Flag prevalence in each book**")
            rows = []
            for f in flag_cols:
                rows.append({
                    "flag": f.replace("flag_", ""),
                    "long %": round(lg[f].mean() * 100, 1) if len(lg) else 0,
                    "short %": round(sh[f].mean() * 100, 1) if len(sh) else 0,
                })
            fp = pd.DataFrame(rows)
            fig = go.Figure()
            fig.add_bar(x=fp["long %"], y=fp["flag"], orientation="h",
                        name="Long", marker_color="#26a69a")
            fig.add_bar(x=fp["short %"], y=fp["flag"], orientation="h",
                        name="Short", marker_color="#ef5350")
            fig.update_layout(barmode="group", height=560,
                              margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="% of book")
            st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- DETAIL
with tab_detail:
    # static label — see the note on the column picker for why
    _tickers = work["ticker"].tolist()
    pick = st.selectbox("Pick a ticker from the filtered set",
                        _tickers if _tickers else [])
    st.caption(f"{len(_tickers):,} name(s) to choose from")
    if pick:
        r = work[work["ticker"] == pick].iloc[0]
        m = st.columns(7)
        m[0].metric("Price", f"${r['close']:,.2f}",
                    f"{r.get('chg_1d_pct', 0):+.2f}%")
        m[1].metric("Side", str(r["side"]), f"score {r['score']:.2f}")
        m[2].metric("RSI(14)", f"{r['rsi14']:.1f}")
        m[3].metric("ADX(14)", f"{r['adx14']:.1f}")
        m[4].metric("ATR%", f"{r['atr_pct']:.2f}%")
        m[5].metric("$vol (M)", f"{r['avg_dollar_vol_m']:,.1f}")
        m[6].metric("Optionable", "yes" if r["is_optionable"] else "no")

        st.markdown(f"### {pick} — {r['name']}")
        st.caption(f"{r['sector']} · {r['universe']} · "
                   f"member of {r['universes']}")

        c1, c2, c3 = st.columns([1.1, 1, 1])
        with c1:
            st.markdown("**Every field**")
            fields = pd.DataFrame({
                "field": [c for c in df_all.columns
                          if c not in ("ticker", "name", "sector", "universe",
                                       "universes", "flags")],
            })
            fields["value"] = [
                (f"{r[c]:.4g}" if isinstance(r[c], (int, float, np.floating,
                                                    np.integer))
                 else str(r[c]))
                for c in fields["field"]]
            st.dataframe(fields, hide_index=True, height=460,
                         use_container_width=True)
        with c2:
            st.markdown("**Model trade construction**")
            lvl = pd.DataFrame({
                "level": ["Entry", "Stop (2×ATR)", "Target (4×ATR)",
                          "Risk % of price", "Shares per 1% risk",
                          "Reward : risk"],
                "value": [
                    f"${r['entry']:,.2f}",
                    f"${r['stop_long'] if r['side']=='LONG' else r['stop_short']:,.2f}",
                    f"${r['target_long'] if r['side']=='LONG' else r['target_short']:,.2f}",
                    f"{r['risk_pct']:.2f}%",
                    f"{int(r['shares_for_1pct_risk']):,}",
                    f"{r['reward_risk']:.1f} R",
                ],
            })
            st.dataframe(lvl, hide_index=True, use_container_width=True)
            st.markdown("**Active setup flags**")
            act = [f.replace("flag_", "") for f in flag_cols if bool(r[f])]
            st.write(", ".join(act) if act else "_none_")
        with c3:
            if flegs:
                st.markdown("**Factor scores (σ)**")
                fl = pd.DataFrame({"factor": [c.replace("f_", "") for c in flegs],
                                   "sigma": [r[c] for c in flegs]}).dropna()
                fl = fl.sort_values("sigma")
                if HAVE_PLOTLY:
                    fig = go.Figure(go.Bar(
                        x=fl["sigma"], y=fl["factor"], orientation="h",
                        marker_color=np.where(fl["sigma"] >= 0, "#26a69a", "#ef5350")))
                    fig.update_layout(height=360, margin=dict(l=10, r=10, t=10, b=10),
                                      xaxis_title="cross-sectional σ")
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.dataframe(fl, hide_index=True)

        st.divider()
        prompt_panel(r, key="detail_prompt", style=prompt_style, source=src,
                     title=f"### 💬 Research prompt — {pick}")
