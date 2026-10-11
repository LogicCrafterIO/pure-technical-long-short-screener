"""
Pure Technical Long/Short Screener - interactive dashboard.

Run with:   python run_dashboard.py       (or)   streamlit run app.py

Tabs
----
  Screener      ranked long/short table with price, liquidity, sector, rank-by
                and optional-flag filters, plus CSV export
  Charts        score distributions, sector and factor diagnostics
  Ticker Detail price chart with the exact indicator overlays the model uses
  Methodology   in-app explanation of every factor and why it is there
"""

from __future__ import annotations

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

import config as C            # noqa: E402
import data as D              # noqa: E402
import screener as S          # noqa: E402
import universe as U          # noqa: E402
from prompt_ui import PROMPT_STYLES, copy_table, prompt_panel  # noqa: E402
from prompt_ui import prompts_frame, rank_first  # noqa: E402

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAVE_PLOTLY = True
except Exception:  # pragma: no cover
    HAVE_PLOTLY = False

st.set_page_config(
    page_title="Pure Technical Long/Short Screener",
    page_icon="📉",
    layout="wide",
    initial_sidebar_state="expanded",
)

SIDE_LABEL = "side"

# The copy-button table embeds every prompt it shows, so it is capped: a full
# brief is ~7.5 KB, and the component is re-sent on each rerun.
COPY_TABLE_MAX = 100

FLAG_HELP = {
    "flag_uptrend": "Close > SMA50 > SMA200 (stacked bullish MAs)",
    "flag_downtrend": "Close < SMA50 < SMA200 (stacked bearish MAs)",
    "flag_golden_cross": "SMA50 crossed above SMA200 within 20 sessions",
    "flag_death_cross": "SMA50 crossed below SMA200 within 20 sessions",
    "flag_macd_bull_cross": "MACD line crossed above its signal within 10 sessions",
    "flag_macd_bear_cross": "MACD line crossed below its signal within 10 sessions",
    "flag_rsi_oversold": "RSI(14) < 30",
    "flag_rsi_overbought": "RSI(14) > 70",
    "flag_rsi2_extreme": "RSI(2) < 5 or > 95 - short-term exhaustion",
    "flag_bb_squeeze": "Bollinger bandwidth in the bottom 20% of its 126-day range",
    "flag_volume_spike": "5-day volume >= 2x the 60-day average",
    "flag_near_52w_high": "Within 2% of the 52-week high",
    "flag_near_52w_low": "Within 2% of the 52-week low",
    "flag_breakout_20d": "Close at/above the prior 20-day Donchian high",
    "flag_breakdown_20d": "Close at/below the prior 20-day Donchian low",
    "flag_new_high_252": "New 252-day closing high",
    "flag_new_low_252": "New 252-day closing low",
    "flag_above_200sma": "Close above the 200-day SMA",
    "flag_gap_up_today": "Opened more than 3% above the previous close",
}

RANK_PRESETS_LONG = {
    "Composite (default weights)": ("long_score", False),
    "Composite + regime tilt": ("long_score_adj", False),
    "Trend quality": ("f_trend_long", False),
    "Relative momentum": ("f_momentum_long", False),
    "Risk-adjusted momentum": ("f_riskadj_long", False),
    "Relative strength vs universe": ("f_relstrength_long", False),
    "Volume / flow confirmation": ("f_volume_long", False),
    "Entry quality (pullback)": ("f_entry_long", False),
    "12-1 momentum (raw %)": ("ret_12_1", False),
    "6-month return (raw %)": ("ret_6m", False),
    "Lowest volatility": ("vol_63", True),
    "Closest to 52w high": ("pct_to_52w_high", False),
    "Lowest ATR% (tightest risk)": ("atr_pct", True),
}
RANK_PRESETS_SHORT = {
    "Composite (default weights)": ("short_score", False),
    "Composite + regime tilt": ("short_score_adj", False),
    "Trend quality": ("f_trend_short", False),
    "Relative momentum": ("f_momentum_short", False),
    "Risk-adjusted momentum": ("f_riskadj_short", False),
    "Relative strength vs universe": ("f_relstrength_short", False),
    "Volume / flow confirmation": ("f_volume_short", False),
    "Entry quality (rally)": ("f_entry_short", False),
    "12-1 momentum (raw %)": ("ret_12_1", True),
    "6-month return (raw %)": ("ret_6m", True),
    "Highest volatility": ("vol_63", False),
    "Closest to 52w low": ("pct_above_52w_low", True),
    "Highest ATR%": ("atr_pct", False),
}


# ==========================================================================
# data loading
# ==========================================================================
@st.cache_data(show_spinner=False)
def get_screen():
    return S.load_latest_screen()


@st.cache_data(show_spinner=False)
def get_prices():
    return D.load_cached_prices()


@st.cache_data(show_spinner=False)
def get_universe():
    return U.load_universe(verbose=False)


# ==========================================================================
# styles
# ==========================================================================
st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 1rem;}
      div[data-testid="stMetricValue"] {font-size: 1.35rem;}
      .regime-pill {
        display:inline-block; padding:4px 14px; border-radius:999px;
        font-weight:700; font-size:0.85rem; letter-spacing:.03em;
      }
      .risk-on  {background:#0f5132; color:#d1f7e0;}
      .risk-off {background:#5c1a1a; color:#ffd9d9;}
      .neutral  {background:#3d3d3d; color:#eee;}
      .small-note {color:#8a8a8a; font-size:0.78rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

df, meta = get_screen()

# ==========================================================================
# sidebar
# ==========================================================================
with st.sidebar:
    st.markdown("### ⚙️ Data")
    c1, c2 = st.columns(2)
    run_clicked = c1.button("▶ Run screen", type="primary", use_container_width=True)
    refresh_clicked = c2.button("↻ Refresh data", use_container_width=True)

    with st.expander("Universe scope", expanded=False):
        uni_labels = list(C.UNIVERSES.keys())
        picked_uni = st.multiselect(
            "Indices included",
            uni_labels,
            default=uni_labels,
            format_func=lambda k: C.UNIVERSES[k],
            help="Changing this re-runs the screen on the selected indices only.",
        )
        force_uni = st.checkbox("Force re-download constituents", value=False)
        force_px = st.checkbox("Force re-download all prices", value=False)
        if force_px:
            st.caption("Full price refresh over ~2,900 tickers takes several minutes.")

    if run_clicked or refresh_clicked:
        bar = st.progress(0.0, text="starting ...")

        def _prog(msg, _state={"i": 0}):
            _state["i"] += 1
            bar.progress(min(0.95, _state["i"] / 12.0), text=msg)

        try:
            S.run_screen(
                labels=picked_uni,
                force_universe=force_uni or refresh_clicked,
                force_prices=force_px or refresh_clicked,
                verbose=False,
                progress=_prog,
            )
            bar.progress(1.0, text="done")
            st.cache_data.clear()
            st.success("Screen updated.")
            st.rerun()
        except Exception as e:  # noqa: BLE001
            bar.empty()
            st.error(f"Screen failed: {type(e).__name__}: {e}")

    if df.empty:
        st.warning("No screen found. Click **Run screen** to build one.")
    st.divider()

    st.markdown("### 🎯 View")
    view = st.radio("Show", ["Longs", "Shorts", "Both"], horizontal=True,
                    label_visibility="collapsed")

    st.markdown("### 💰 Price filter")
    if not df.empty and df["close"].notna().any():
        pc = df["close"].dropna()
        lo = float(np.floor(max(0.0, pc.min())))
        hi = float(np.ceil(max(pc.quantile(0.995), lo + 1.0)))
        d_lo = float(np.clip(C.DEFAULT_MIN_PRICE, lo, hi))
        d_hi = float(np.clip(C.DEFAULT_MAX_PRICE, d_lo, hi))
        max_price = st.slider(
            "Price range ($)", min_value=lo, max_value=hi,
            value=(d_lo, d_hi), step=max(0.5, round((hi - lo) / 400, 2)),
        )
    else:
        max_price = (float(C.DEFAULT_MIN_PRICE), float(C.DEFAULT_MAX_PRICE))
    min_price, max_price_hi = max_price

    st.markdown("### 📊 Liquidity & risk filters")
    min_dv = st.slider("Min 20-day $ volume ($M)", 0.0, 100.0,
                       float(C.MIN_DOLLAR_VOLUME / 1e6), 0.5)
    max_atr = st.slider("Max ATR% of price", 1.0, 30.0, 30.0, 0.5,
                        help="Caps how much daily range you are willing to hold.")
    adx_range = st.slider("ADX(14) range", 0.0, 60.0, (0.0, 60.0), 1.0,
                          help="Higher ADX = stronger, more directional trend.")
    rsi_range = st.slider("RSI(14) range", 0.0, 100.0, (0.0, 100.0), 1.0)

    st.markdown("### 🏷️ Optional flags")
    all_flags = sorted([c for c in df.columns if c.startswith("flag_")]) if not df.empty else []
    inc_flags = st.multiselect(
        "Must have these flags", all_flags,
        format_func=lambda f: f.replace("flag_", "").replace("_", " "),
        help="Tick one or more setups to require.",
    )
    flag_mode = st.radio("Flag logic", ["ALL (AND)", "ANY (OR)"], horizontal=True)
    exc_flags = st.multiselect(
        "Must NOT have these flags", all_flags,
        format_func=lambda f: f.replace("flag_", "").replace("_", " "),
        help="Use to exclude setups you do not want, e.g. death_cross.",
    )
    only_optionable = st.checkbox(
        "Optionable only", value=False,
        help="Liquidity/price proxy for listed options: price >= $5 and "
             "20-day dollar volume >= $5M. Optionable names are also the "
             "ones that are reliably shortable.",
    )
    for f in inc_flags:
        st.caption(f"• **{f.replace('flag_','')}** — {FLAG_HELP.get(f,'')}")

    st.divider()
    st.markdown("### 🧮 Rank by")
    side_key = "long" if view == "Longs" else ("short" if view == "Shorts" else "long")

    with st.expander("⚖️ Custom factor weights", expanded=False):
        st.caption("Replace the composite score with your own group weights. "
                   "Each group is already a cross-sectional Gaussian rank, so "
                   "the weights are directly comparable.")
        st.session_state.setdefault(
            "w",
            {**C.LONG_WEIGHTS,
             **{k: v for k, v in C.SHORT_WEIGHTS.items() if k not in C.LONG_WEIGHTS}},
        )
        w = st.session_state["w"]
        new_w = {}
        group_keys = list(dict.fromkeys(list(C.LONG_WEIGHTS) + list(C.SHORT_WEIGHTS)))
        for g in group_keys:
            new_w[g] = st.slider(g.replace("_", " "), 0.0, 0.60,
                                 float(w.get(g, 0.0)), 0.01, key=f"w_{g}")
        use_custom = st.checkbox("Apply custom weights to composite score",
                                 value=False)
        if st.button("Reset weights to defaults"):
            st.session_state["w"] = {
                **C.LONG_WEIGHTS,
                **{k: v for k, v in C.SHORT_WEIGHTS.items()
                   if k not in C.LONG_WEIGHTS},
            }
            st.rerun()

    preset_map = dict(RANK_PRESETS_LONG if side_key == "long" else RANK_PRESETS_SHORT)
    if use_custom:
        preset_map = {"★ Custom weights": (f"{side_key}_score_custom", False),
                      **preset_map}
    rank_preset = st.selectbox("Ranking metric", list(preset_map.keys()), index=0)

    st.divider()
    st.markdown("### 💬 Research prompts")
    st.session_state.setdefault("prompt_style", "full")
    st.radio("Prompt style", list(PROMPT_STYLES), key="prompt_style",
             format_func=lambda k: PROMPT_STYLES[k],
             label_visibility="collapsed",
             help="What every 'copy prompt' block quotes.")
    st.caption("The screen is technical only. These prompts ask a chatbot for the "
               "**fundamental story** behind the move and whether it is likely to "
               "continue over the next 1-3 months. Grab one per ticker with the "
               "**📋 Copy** button on every row of **🔎 Screener**, or read the full text in "
               "the **💬 Research** / **🕯️ Ticker detail** tabs.")

    st.divider()
    top_n = st.slider("Rows to show", 10, 500, C.DEFAULT_TOP_N, 10)
    st.caption(
        f"<span class='small-note'>Run at {meta.get('run_at','-')} • "
        f"data as of {meta.get('as_of','-')}</span>",
        unsafe_allow_html=True,
    )


# ==========================================================================
# header
# ==========================================================================
# settings shared by every copy-prompt block below (set in the sidebar)
prompt_style = st.session_state.get("prompt_style", "full")
as_of = meta.get("as_of") or None
prompt_source = f"screen run {meta.get('as_of', '-')}"

st.title("📉 Pure Technical Long/Short Screener")

regime = (meta.get("regime") or {})
r = regime.get("regime", "unknown")
pill_cls = {"risk_on": "risk-on", "risk_off": "risk-off"}.get(r, "neutral")
pill_txt = {"risk_on": "RISK ON", "risk_off": "RISK OFF"}.get(r, "NO DATA")
breadth = regime.get("breadth_pct")

h1, h2, h3, h4, h5 = st.columns([1.1, 1, 1, 1, 1])
h1.markdown(
    f"<div class='regime-pill {pill_cls}'>{pill_txt}</div>"
    f"<div class='small-note'>SPY {regime.get('spy_dist_pct', float('nan')):.2f}% vs 200SMA"
    f"</div>" if np.isfinite(regime.get("spy_dist_pct", np.nan)) else
    f"<div class='regime-pill neutral'>NO DATA</div>",
    unsafe_allow_html=True,
)
h2.metric("Universe scanned", f"{meta.get('n_universe', 0):,}")
h3.metric("Passed liquidity gates", f"{meta.get('n_eligible', 0):,}")
h4.metric("Breadth > 50SMA", f"{breadth:.0f}%" if breadth is not None and np.isfinite(breadth) else "-")
h5.metric("Screen as of", meta.get("as_of", "-"))

if df.empty:
    st.info("No screen data yet. Use **▶ Run screen** in the sidebar — the first "
            "run downloads ~2,900 tickers from yfinance and takes a few minutes.")
    st.stop()


# ==========================================================================
# filtering
# ==========================================================================
work = df[df["eligible"]].copy()
if "universes" in work.columns:
    work = work[work["universes"].apply(
        lambda s: any(l in str(s).split("|") for l in picked_uni)
    )]

work = work[(work["close"] >= min_price) & (work["close"] <= max_price_hi)]
work = work[work["avg_dollar_vol_20"] >= min_dv * 1e6]
work = work[work["atr_pct"].fillna(999) <= max_atr]
work = work[(work["adx14"].fillna(0) >= adx_range[0]) & (work["adx14"].fillna(0) <= adx_range[1])]
work = work[(work["rsi14"].fillna(50) >= rsi_range[0]) & (work["rsi14"].fillna(50) <= rsi_range[1])]
if only_optionable:
    work = work[work["is_optionable"]]

if inc_flags:
    mask = work[inc_flags].fillna(False)
    work = work[mask.all(axis=1)] if flag_mode.startswith("ALL") else work[mask.any(axis=1)]
if exc_flags:
    work = work[~work[exc_flags].fillna(False).any(axis=1)]

# --- custom weights ------------------------------------------------------
# Maps a factor-group name to its column template.  `squeeze_penalty` exists
# only on the short side and enters with a NEGATIVE sign (it is a deduction).
GROUP_COL = {
    "trend": "f_trend_{side}",
    "momentum": "f_momentum_{side}",
    "risk_adj_mom": "f_riskadj_{side}",
    "rel_strength": "f_relstrength_{side}",
    "volume": "f_volume_{side}",
    "entry": "f_entry_{side}",
    "squeeze_penalty": "f_squeeze_penalty",
}
GROUP_SIGN = {"squeeze_penalty": -1.0}

if use_custom and any(v > 0 for v in new_w.values()):

    def compose(side):
        num = den = None
        for g, wv in new_w.items():
            if wv <= 0:
                continue
            col = GROUP_COL.get(g, "").format(side=side)
            if col not in df.columns:
                continue
            s = df[col] * GROUP_SIGN.get(g, 1.0)
            m = s.notna().astype(float) * wv
            num = (s * wv) if num is None else num.add(s * wv, fill_value=0.0)
            den = m if den is None else den.add(m, fill_value=0.0)
        return (num / den.replace(0.0, np.nan)) if num is not None else None

    for side in ("long", "short"):
        cs = compose(side)
        if cs is not None:
            df[f"{side}_score_custom"] = cs
            work[f"{side}_score_custom"] = cs.reindex(df.index).reindex(work.index)

metric_col, metric_asc = preset_map[rank_preset]

# --- side selection ------------------------------------------------------
if view == "Both":
    half = max(1, top_n // 2)
    lg = work.sort_values("long_score", ascending=False).head(half).copy()
    sh = work.sort_values("short_score", ascending=False).head(half).copy()
    lg["side"], sh["side"] = "LONG", "SHORT"
    lg["score"] = lg["long_score"]; sh["score"] = sh["short_score"]
    view_df = pd.concat([lg, sh], ignore_index=True)
    view_df = view_df.sort_values("score", ascending=False).reset_index(drop=True)
else:
    view_df = work.copy()
    view_df["side"] = "LONG" if view == "Longs" else "SHORT"
    sort_col = metric_col if metric_col in view_df.columns else (
        "long_score" if side_key == "long" else "short_score")
    view_df = view_df.sort_values(sort_col, ascending=metric_asc,
                                  na_position="last")
    view_df["score"] = view_df[sort_col]
    view_df = view_df.head(top_n).reset_index(drop=True)

view_df.insert(0, "rank", np.arange(1, len(view_df) + 1))

# resolve per-side columns so one table layout works for longs, shorts and both
_is_long = view_df["side"].eq("LONG")
view_df["stop"] = np.where(_is_long, view_df["stop_long"], view_df["stop_short"])
view_df["target"] = np.where(_is_long, view_df["target_long"], view_df["target_short"])
view_df["shares_for_1pct_risk"] = (
    view_df["shares_for_1pct_risk"].fillna(0).astype("int64"))


# ==========================================================================
# tabs
# ==========================================================================
tab_screen, tab_research, tab_charts, tab_detail, tab_method = st.tabs(
    ["🔎 Screener", "💬 Research", "📊 Charts", "🕯️ Ticker detail", "📚 Methodology"]
)

with tab_screen:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Rows after filters", f"{len(view_df):,}")
    k2.metric("Median ATR%",
              f"{view_df['atr_pct'].median():.2f}%" if len(view_df) else "-")
    k3.metric("Median $vol (M)",
              f"{view_df['avg_dollar_vol_m'].median():.1f}" if len(view_df) else "-")
    k4.metric("Median RSI",
              f"{view_df['rsi14'].median():.1f}" if len(view_df) else "-")

    # score immediately left of the ticker, so the ranking reads as one unit
    show_cols = rank_first([c for c in C.DISPLAY_COLUMNS if c in view_df.columns],
                           "score", "ticker")
    extra = [c for c in view_df.columns if c.startswith("f_") and c.endswith(side_key)]
    st.caption(f"Ranked by **{rank_preset}** • {len(view_df)} rows • "
               f"{len(all_flags)} optional flags available")

    # The ranked grid *is* the copy table: same columns the sidebar picked,
    # with a 📋 button on every row that copies that ticker's research prompt.
    copy_table(view_df, cols=show_cols, style=prompt_style,
               as_of=as_of, source=prompt_source, max_rows=COPY_TABLE_MAX,
               height=min(900, 92 + 33 * len(view_df)),
               hint="**📋 Copy** on any row puts that ticker's whole research prompt "
                    "on the clipboard — one click, then paste it into ChatGPT / "
                    "Claude / Gemini / Perplexity. Click a header to sort this page. "
                    "To read a prompt in full, or keep it as a .txt file, use the "
                    "**💬 Research** tab or **🕯️ Ticker detail**.")

    d1, d2 = st.columns([1, 3])
    d1.download_button(
        "⬇ Download filtered CSV",
        view_df.to_csv(index=False).encode("utf-8"),
        file_name=f"screen_{meta.get('as_of','na')}_{side_key}.csv",
        mime="text/csv",
        use_container_width=True,
    )
    with d2.expander("Full column set — every raw value (unformatted)"):
        st.caption("The 134 CSV columns exactly as stored, next to the formatted "
                   "version above. Use ⬇ for the file itself.")
        st.dataframe(view_df[[c for c in view_df.columns if not c.startswith("_")]],
                     use_container_width=True, hide_index=True, height=300)


with tab_research:
    st.markdown("### 💬 Take one ticker to a chatbot")
    st.caption("This screen is **pure technical** — it can tell you what the tape is "
               "doing, never why. The prompt below is the bridge: it hands the "
               "ticker's own numbers to ChatGPT / Claude / Gemini / Perplexity and "
               "asks the two questions the screen cannot answer — **what was the "
               "fundamental story behind this share price move**, and **is it likely "
               "to continue over the next 1-3 months, and why**.")

    _tickers = view_df["ticker"].tolist()
    pick_res = st.selectbox(
        "Ticker to research", _tickers, index=0, key="res_ticker",
        help="Any name in the current view — the ranking is the order of this "
             "dropdown.",
    )
    st.caption(f"{len(_tickers):,} name(s) in the current view · prompt style is set "
               f"in the sidebar under **💬 Research prompts**")
    if pick_res:
        prompt_panel(view_df[view_df["ticker"] == pick_res].iloc[0], key="res_prompt",
                     style=prompt_style, as_of=as_of, source=prompt_source)

    st.divider()
    with st.expander("📦 Batch — prompts for many tickers in one CSV", expanded=False):
        st.caption("One row per ticker with the prompt in the last column, so you can "
                   "work through a shortlist in a spreadsheet or paste them in "
                   "batches.")
        b1, b2 = st.columns([1, 2])
        b1.number_input("How many names (from the top of the view)", 1, 250,
                        value=min(25, max(1, len(_tickers))), step=5,
                        key="res_batch_n")
        b2.radio("Batch style", list(PROMPT_STYLES), key="res_batch_style",
                 format_func=lambda k: PROMPT_STYLES[k], horizontal=True,
                 label_visibility="collapsed")
        _n = int(st.session_state["res_batch_n"])
        batch = prompts_frame(view_df, as_of=as_of, source=prompt_source, limit=_n,
                              style=st.session_state["res_batch_style"])
        st.download_button(f"⬇ Download {len(batch):,} prompt(s) as CSV",
                           batch.to_csv(index=False).encode("utf-8"),
                           file_name=f"research_prompts_{len(batch)}rows.csv",
                           mime="text/csv")
        st.dataframe(batch[["ticker", "name", "sector", "side", "score"]],
                     hide_index=True, use_container_width=True, height=220,
                     column_config={"score": st.column_config.NumberColumn(
                         "score", format="%.2f")})


with tab_charts:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Composite score distribution (filtered set)**")
        s = view_df["score"].dropna()
        if len(s):
            edges = np.linspace(float(s.min()), float(s.max()), 31)
            counts, _ = np.histogram(s, bins=edges)
            st.bar_chart(
                pd.DataFrame({"candidates": counts},
                             index=np.round(edges[:-1], 2)), height=280)
        else:
            st.info("No rows after filters.")
    with c2:
        st.markdown("**Candidates by sector**")
        if len(view_df):
            vc = view_df["sector"].value_counts().head(15)
            vc.index = [str(i)[:26] for i in vc.index]
            st.bar_chart(vc, height=280)
        else:
            st.info("No rows after filters.")

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**Momentum vs risk (bubble = $ volume, colour = score)**")
        if len(view_df) and HAVE_PLOTLY:
            fig = go.Figure(go.Scatter(
                x=view_df["vol_63"], y=view_df["ret_6m"],
                mode="markers+text",
                text=view_df["ticker"], textposition="top center",
                marker=dict(
                    size=np.clip(np.sqrt(view_df["avg_dollar_vol_m"].fillna(1)), 4, 40),
                    color=view_df["score"], colorscale="RdYlGn", showscale=True,
                    colorbar=dict(title="score"),
                ),
            ))
            fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="63d realised vol (%)",
                              yaxis_title="6-month return (%)")
            st.plotly_chart(fig, use_container_width=True)
        elif len(view_df):
            st.scatter_chart(view_df, x="vol_63", y="ret_6m", height=380)
    with c4:
        st.markdown("**Factor contribution profile (top 15)**")
        if len(view_df) and HAVE_PLOTLY and extra:
            top = view_df.head(15)[["ticker"] + extra].set_index("ticker")
            fig = go.Figure()
            for c in extra:
                fig.add_bar(x=top.index, y=top[c],
                            name=c.replace("f_", "").replace(f"_{side_key}", ""))
            fig.update_layout(barmode="relative", height=380,
                              margin=dict(l=10, r=10, t=10, b=10),
                              legend=dict(orientation="h", y=-0.25))
            st.plotly_chart(fig, use_container_width=True)
        elif len(view_df):
            st.bar_chart(view_df.set_index("ticker")[extra].head(15), height=380)
        else:
            st.info("No rows after filters.")


with tab_detail:
    if view_df.empty:
        st.info("No rows to inspect.")
    else:
        pick = st.selectbox("Ticker", view_df["ticker"].tolist(), index=0)
        row = view_df[view_df["ticker"] == pick].iloc[0]
        prices = get_prices()
        if not prices or pick not in prices["close"].columns:
            st.warning("Price history not available for this ticker in the cache.")
        else:
            close = prices["close"][pick].dropna()
            high = prices["high"][pick].reindex(close.index).fillna(close)
            low = prices["low"][pick].reindex(close.index).fillna(close)
            vol = prices["volume"][pick].reindex(close.index).fillna(0)
            look = close.tail(260)
            s20 = look.rolling(20).mean(); s50 = look.rolling(50).mean()
            s200 = look.rolling(200).mean()

            if HAVE_PLOTLY:
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                    row_heights=[0.75, 0.25], vertical_spacing=0.04)
                fig.add_trace(go.Candlestick(
                    x=look.index, open=look.values, high=high.tail(260).values,
                    low=low.tail(260).values, close=look.values, name=pick,
                    increasing_line_color="#26a69a", decreasing_line_color="#ef5350"),
                    row=1, col=1)
                for s, nm, col in ((s20, "SMA20", "#ffb74d"), (s50, "SMA50", "#4fc3f7"),
                                   (s200, "SMA200", "#ba68c8")):
                    fig.add_trace(go.Scatter(x=look.index, y=s, name=nm,
                                             line=dict(width=1.4, color=col),),
                                  row=1, col=1)
                fig.add_trace(go.Bar(x=look.index, y=vol.tail(260).values,
                                     name="Volume", marker_color="#546e7a"), row=2, col=1)
                fig.update_layout(height=560, margin=dict(l=10, r=10, t=30, b=10),
                                  xaxis_rangeslider_visible=False,
                                  legend=dict(orientation="h", y=1.06))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.line_chart(pd.DataFrame({"Close": look, "SMA20": s20,
                                            "SMA50": s50, "SMA200": s200}))

            m = st.columns(6)
            m[0].metric("Price", f"${row['close']:.2f}", f"{row.get('chg_1d_pct',0):.2f}%")
            m[1].metric("Side", row["side"], f"score {row['score']:.2f}")
            m[2].metric("RSI(14)", f"{row['rsi14']:.1f}")
            m[3].metric("ADX(14)", f"{row['adx14']:.1f}")
            m[4].metric("ATR%", f"{row['atr_pct']:.2f}%")
            m[5].metric("RVOL", f"{row['rvol']:.2f}")

            lv = pd.DataFrame({
                "Level": ["Entry", "Stop (2xATR)", "Target (4xATR)", "Trail (3xATR)",
                          "Risk %", "Shares per 1% risk"],
                "Value": [row["entry"],
                          row["stop_long"] if row["side"] == "LONG" else row["stop_short"],
                          row["target_long"] if row["side"] == "LONG" else row["target_short"],
                          row["trail_long"] if row["side"] == "LONG" else row["trail_short"],
                          row["risk_pct"], row["shares_for_1pct_risk"]],
            })
            a, b = st.columns([1, 1.4])
            a.markdown("**Model trade construction**")
            a.dataframe(lv, hide_index=True, use_container_width=True)
            b.markdown("**Active setup flags**")
            act = [f.replace("flag_", "") for f in all_flags if bool(row.get(f, False))]
            b.write(", ".join(act) if act else "_none_")
            b.markdown("**Factor scores (cross-sectional rank units)**")
            b.dataframe(
                pd.DataFrame({
                    "factor": [c.replace("f_", "").replace(f"_{side_key}", "")
                               for c in extra],
                    "z": [row[c] for c in extra],
                }).round(2),
                hide_index=True, use_container_width=True,
            )

            st.divider()
            prompt_panel(row, key="detail_prompt", style=prompt_style, as_of=as_of,
                         source=prompt_source,
                         title=f"### 💬 Research prompt — {pick}")


with tab_method:
    st.markdown(
        f"""
### What this screen actually measures

Every number below is derived **only** from price and volume history — no
fundamentals, no estimates, no news.  Full worked justification is in
`TECHNICAL_JUSTIFICATION.md`.

**Step 1 — Eligibility gates.** A ticker must trade above ${C.MIN_PRICE:.0f},
turn over at least ${C.MIN_DOLLAR_VOLUME/1e6:.0f}M/day, and carry at least
{C.MIN_BARS_REQUIRED} sessions of history.  This is not a view on quality; it is
a statement that below these levels execution costs, gap risk and the inability
to borrow make any signal untradeable.

**Step 2 — Cross-sectional normalisation.** Every continuous factor is
converted to the Gaussian rank of its cross-sectional percentile
(Blom transform, clipped at ±{C.WINSOR_Z}).  A raw RSI of 62 or a 6-month
return of 30% is meaningless in isolation; a *rank* against the other 2,900
names on the same day is not.  Ranks also make the factors outlier-proof and
automatically regime-neutral.

**Step 3 — Six factor groups.**

| Group | Weight (long) | What it captures |
|---|---|---|
| Trend structure | {C.LONG_WEIGHTS['trend']:.0%} | MA stack, distance to SMA50/200, both MA slopes, signed ADX |
| Relative momentum | {C.LONG_WEIGHTS['momentum']:.0%} | 12-1, 6m, 3m total return |
| Risk-adjusted momentum | {C.LONG_WEIGHTS['risk_adj_mom']:.0%} | the same returns per unit of realised volatility |
| Relative strength | {C.LONG_WEIGHTS['rel_strength']:.0%} | excess return vs the universe, up-day ratio |
| Volume / flow | {C.LONG_WEIGHTS['volume']:.0%} | OBV slope, Chaikin Money Flow, MFI, VWAP distance, RVOL |
| Entry quality | {C.LONG_WEIGHTS['entry']:.0%} | rewards the shallow pullback, not the extended breakout |

**Step 4 — Absolute risk controls.** A rank can never say "this is simply too
far gone".  So two *absolute* adjustments are applied on top: longs stretched
more than {C.OVEREXTENSION_START_ATR} ATR above their 20-day mean are penalised
(up to −{C.OVEREXTENSION_MAX_PENALTY}), and shorts in names above ~8–20% daily
ATR are progressively avoided.

**Step 5 — The short side is not the negation of the long side.** Equity
downside is structurally different: volatility is asymmetric, squeezes are
unbounded, and borrow is constrained.  So the short score carries an explicit
squeeze penalty (volume spikes, one-month run-ups, big up days) and a
high-volatility penalty on top of the mirrored factors.

**Step 6 — Trade construction.** Stops are {C.STOP_ATR_MULT}x ATR(14), targets
{C.TARGET_ATR_MULT}x ATR(14) — a fixed {C.TARGET_ATR_MULT/C.STOP_ATR_MULT:.0f}R
geometry where position size, not conviction, normalises risk.  Share counts
assume {C.ACCOUNT_EQUITY:,.0f} equity risking {C.ACCOUNT_RISK_PCT:.0%} per name.

**Current market regime:** `{r}` — SPY is
{regime.get('spy_dist_pct', float('nan')):.2f}% versus its {C.REGIME_SMA}-day SMA,
and {breadth if breadth is None else round(breadth)}% of eligible names sit above
their own 50-day SMA.
        """
    )
    st.info(
        "**Optional flags.** The flag filter is optional by design: leave it "
        "empty to rank the full cross-section, or require/exclude specific "
        "setups (golden cross, squeeze, breakout, oversold, ...) to isolate a "
        "playbook.  `Optionable only` applies the price/liquidity proxy for "
        "listed options, which is also the practical gate for shortability."
    )
