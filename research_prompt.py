"""
Copy-paste research prompts for a single screened ticker.

The screener is *pure technical*: every number it produces comes from price and
volume history.  That answers "what is happening" but never "why".  This module
turns one screen row into a ready-to-paste prompt for an LLM chat (ChatGPT,
Claude, Gemini, Perplexity, ...) that asks the question the model cannot:

    what was the fundamental story behind this share price move, and is it
    likely to continue over the next 1-3 months?

Everything here is pure Python (no Streamlit), so the prompt text can be built
from a CSV row, a dict, or one row of a DataFrame in exactly the same way.

    from research_prompt import build_prompt, build_short_prompt, prompts_frame

    text = build_prompt(df.iloc[0], as_of="2026-10-09")
"""

from __future__ import annotations

import math
from typing import Any, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Default horizon used in the wording of the prompt.
DEFAULT_HORIZON = "the next 1-3 months"

# Kept deliberately short: it is the part a reader is most likely to trim.
STANDING_CAVEAT = (
    "Where you are unsure, say so rather than guessing, and mark anything you "
    "could not verify. Do not give me generic investing education, and do not "
    "simply restate the snapshot below back to me."
)


# ==========================================================================
# tolerant field access — rows arrive as pandas Series, dicts or records
# ==========================================================================
def _get(row: Any, key: str, default: Any = None) -> Any:
    """`row[key]` for a Series/dict/record, treating missing and blank as None."""
    v = None
    try:
        v = row[key]
    except Exception:  # noqa: BLE001 - KeyError, TypeError, IndexError, ...
        try:
            v = row.get(key, default)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return default
    if v is None:
        return default
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return default
    try:
        if pd.isna(v):
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(v, str) and not v.strip():
        return default
    return v


def _num(row: Any, key: str) -> Optional[float]:
    v = _get(row, key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _s(row: Any, key: str, default: str = "") -> str:
    v = _get(row, key)
    return default if v is None else str(v).strip()


# ==========================================================================
# formatting
# ==========================================================================
def _money(v: Optional[float], dp: int = 2) -> Optional[str]:
    return None if v is None else f"${v:,.{dp}f}"


def _pct(v: Optional[float], dp: int = 1, signed: bool = True) -> Optional[str]:
    if v is None:
        return None
    return f"{v:+.{dp}f}%" if signed else f"{v:.{dp}f}%"


def _ratio(v: Optional[float], dp: int = 2) -> Optional[str]:
    return None if v is None else f"{v:.{dp}f}"


def _plain(v: Optional[float], dp: int = 1) -> Optional[str]:
    return None if v is None else f"{v:,.{dp}f}"


def _frac_pct(v: Optional[float], dp: int = 1) -> Optional[str]:
    """For columns stored as a 0-1 fraction (up-day ratio, Bollinger %B...)."""
    v = _to_float(v)
    return None if v is None else f"{v * 100:.{dp}f}%"


def _join(sep: str, *vals: Optional[str]) -> Optional[str]:
    """Join the non-empty values, e.g. "$1.20 / $1.05"; None when all are empty."""
    keep = [str(v) for v in vals if v not in (None, "")]
    return sep.join(keep) if keep else None


def _ordinal(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _pctile(v: Optional[float]) -> Optional[str]:
    """For rolling-percentile columns stored 0-1."""
    v = _to_float(v)
    return None if v is None else f"{_ordinal(int(round(v * 100)))} percentile"


def _slope(v: Optional[float], window: str) -> Optional[str]:
    """`slope_pct` columns: fractional change over the window."""
    v = _to_float(v)
    return None if v is None else f"{v * 100:+.1f}% over {window}"


def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


def _yn(v: Any) -> str:
    if v is None:
        return "unknown"
    if isinstance(v, str):
        return "yes" if v.strip().lower() in ("1", "true", "yes", "y") else "no"
    return "yes" if bool(v) else "no"


def _block(pairs: Sequence[Tuple[str, Optional[str]]], indent: str = "  ") -> List[str]:
    """Render (label, value) pairs left-aligned, dropping empty values."""
    keep = [(lbl, str(val)) for lbl, val in pairs if val not in (None, "")]
    if not keep:
        return []
    width = max(len(lbl) for lbl, _ in keep)
    return [f"{indent}{lbl.ljust(width)}  {val}" for lbl, val in keep]


def _section(out: List[str], title: Optional[str], pairs: Sequence[Tuple[str, Optional[str]]]) -> None:
    """Append a blank line, an optional header, then the non-empty pairs."""
    body = _block(pairs)
    if not body:
        return
    out.append("")
    if title:
        out.append(f"  {title}")
    out.extend(body)


# ==========================================================================
# narrative helpers
# ==========================================================================
def _side_word(row: Any) -> str:
    side = _s(row, "side").upper()
    return "short" if side.startswith("SHORT") else "long"


def _move_phrase(row: Any) -> str:
    """A short human description of the price action, e.g. 'up 29% over 6m'."""
    bits = []
    for key, lbl in (("ret_1m", "1m"), ("ret_3m", "3m"), ("ret_6m", "6m"),
                     ("ret_12m", "12m")):
        v = _num(row, key)
        if v is not None:
            bits.append(f"{v:+.0f}% over {lbl}")
    if not bits:
        return "moving"
    # Prefer the medium-term window when quoting a single number.
    v6 = _num(row, "ret_6m")
    if v6 is not None:
        return ("up " if v6 >= 0 else "down ") + f"{abs(v6):.0f}% over six months"
    return bits[0]


def _active_flags(row: Any, flag_cols: Optional[Iterable[str]] = None) -> List[str]:
    """Flag names that are on. Uses the `flags` string when present, else the
    flag_* columns."""
    raw = _get(row, "flags")
    if isinstance(raw, str) and raw.strip():
        return [f.strip() for f in raw.replace("|", ",").split(",") if f.strip()]
    if flag_cols is None:
        try:
            flag_cols = [c for c in row.index if str(c).startswith("flag_")]  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            flag_cols = []
    return [str(c).replace("flag_", "") for c in flag_cols
            if _get(row, c) not in (None, False, 0, "0", "False")]


def _score_sentence(row: Any) -> str:
    side = _side_word(row)
    out = []
    if side == "short":
        sc, pct, rk = _num(row, "short_score"), _num(row, "short_pct"), _num(row, "rank_short")
        label = "short"
    else:
        sc, pct, rk = _num(row, "long_score"), _num(row, "long_pct"), _num(row, "rank_long")
        label = "long"
    if sc is not None:
        out.append(f"composite {label} score {sc:+.2f} sigma")
    if pct is not None:
        out.append(f"{pct:.0f}th percentile of the cross-section")
    if rk is not None:
        out.append(f"rank {rk:,.0f} of the names that passed the liquidity gates")
    return ", ".join(out) if out else "no composite score available"


# ==========================================================================
# the snapshot that goes inside the prompt
# ==========================================================================
def snapshot_lines(row: Any, as_of: Optional[str] = None) -> List[str]:
    """The technical snapshot, as ready-to-paste lines."""
    ticker = _s(row, "ticker", "?")
    name = _s(row, "name", "unknown company")
    sector = _s(row, "sector", "unknown sector")
    universes = _s(row, "universes") or _s(row, "universe", "-")
    data_date = as_of or _s(row, "last_date") or "-"
    side = _s(row, "side", "-").upper()

    out: List[str] = []
    out.append(f"  ticker / company     {ticker} - {name}")
    out.append(f"  sector / index       {sector} | {universes}")
    last = _s(row, "last_date")
    stamp = data_date if not (as_of and last and as_of != last) else f"{last} (screen run {as_of})"
    out.append(f"  data as of           {stamp}")
    out.append(f"  screen classification {side} candidate - {_score_sentence(row)}")
    out.append(f"  optionable           {_yn(_get(row, 'is_optionable'))} "
               "[price/volume proxy, not an options-chain lookup]")

    price = _num(row, "close")
    _section(out, None, [
        ("price", _money(price)),
        ("change today", _pct(_num(row, "chg_1d_pct"), 2)),
        ("52-week high / low",
         _join(" / ", _money(_num(row, "hi_52w")), _money(_num(row, "lo_52w")))),
        ("position in 52w range",
         _pct(_num(row, "range_pos_52w"), 0, signed=False) + " (0 = low, 100 = high)"
         if _num(row, "range_pos_52w") is not None else None),
        ("distance to 52w high", _pct(_num(row, "pct_to_52w_high"), 1)),
        ("above 52w low by", _pct(_num(row, "pct_above_52w_low"), 1)),
    ])

    _section(out, "Returns and momentum", [
        ("1 month", _pct(_num(row, "ret_1m"))),
        ("3 months", _pct(_num(row, "ret_3m"))),
        ("6 months", _pct(_num(row, "ret_6m"))),
        ("12 months", _pct(_num(row, "ret_12m"))),
        ("12-1 momentum", _pct(_num(row, "ret_12_1"))),
        ("excess vs universe 1m/3m/6m",
         _join(" / ", _pct(_num(row, "rs_ret_1m")), _pct(_num(row, "rs_ret_3m")),
               _pct(_num(row, "rs_ret_6m")))),
        ("6m return per unit of vol", _ratio(_num(row, "mom6_over_vol"), 2)),
        ("share of up days (63d)", _frac_pct(_num(row, "up_day_ratio_63"))),
    ])

    _section(out, "Trend and position versus moving averages", [
        ("SMA20 / SMA50 / SMA200",
         _join(" / ", _money(_num(row, "sma20")), _money(_num(row, "sma50")),
               _money(_num(row, "sma200")))),
        ("distance from SMA20", _pct(_num(row, "dist_sma20_pct"))),
        ("distance from SMA50", _pct(_num(row, "dist_sma50_pct"))),
        ("distance from SMA200", _pct(_num(row, "dist_sma200_pct"))),
        ("20-day stretch (in ATRs)", _ratio(_num(row, "stretch_atr_20"), 2)),
        ("SMA50 slope (63d)", _slope(_num(row, "sma50_slope"), "63d")),
        ("SMA200 slope (126d)", _slope(_num(row, "sma200_slope"), "126d")),
        ("ADX(14) trend strength", _ratio(_num(row, "adx14"), 1)),
        ("+DI / -DI", _join(" / ", _ratio(_num(row, "plus_di"), 1),
                            _ratio(_num(row, "minus_di"), 1))),
        ("MACD histogram (% of price)", _ratio(_num(row, "macd_hist_pct"), 3)),
    ])

    _section(out, "Short-term timing (mean-reversion risk)", [
        ("RSI(14) / RSI(2)", _join(" / ", _ratio(_num(row, "rsi14"), 1),
                                   _ratio(_num(row, "rsi2"), 1))),
        ("Stochastic %K / Williams %R",
         _join(" / ", _ratio(_num(row, "stoch_k"), 1),
               _ratio(_num(row, "williams_r"), 1))),
        ("Bollinger %B (0-1 band position)", _ratio(_num(row, "pct_b"), 2)),
        ("Bollinger bandwidth", _pctile(_num(row, "bbw_pctile")) + " of its own 1-year range"
         if _num(row, "bbw_pctile") is not None else None),
        ("20-day z-score", _ratio(_num(row, "zscore_20"), 2)),
        ("21d realised vol", _pctile(_num(row, "vol_pctile")) + " of its own 1-year range"
         if _num(row, "vol_pctile") is not None else None),
    ])

    _section(out, "Volume, liquidity and flow", [
        ("avg daily $ volume (20d)",
         (f"${_num(row, 'avg_dollar_vol_m'):,.0f}M"
          if _num(row, "avg_dollar_vol_m") is not None else None)),
        ("relative volume", _ratio(_num(row, "rvol"), 2)),
        ("Chaikin money flow (21d, -1..1)", _ratio(_num(row, "cmf_21"), 3)),
        ("MFI(14)", _ratio(_num(row, "mfi14"), 1)),
        ("OBV 63d slope (avg-daily-volume units)", _ratio(_num(row, "obv_slope_63"), 3)),
        ("distance from 20d VWAP", _pct(_num(row, "vwap20_dist_pct"), 2)),
        ("realised vol 21d / 63d",
         _join(" / ", _pct(_num(row, "vol_21"), 1, signed=False),
               _pct(_num(row, "vol_63"), 1, signed=False))),
    ])

    side = _side_word(row)
    stop = _num(row, "stop_short" if side == "short" else "stop_long")
    target = _num(row, "target_short" if side == "short" else "target_long")
    trail = _num(row, "trail_short" if side == "short" else "trail_long")
    _section(out, "Risk and the model's own trade plan", [
        ("ATR(14)", _join(" ", _money(_num(row, "atr14")),
                          "(" + _pct(_num(row, "atr_pct"), 2, signed=False) + " of price)"
                          if _num(row, "atr_pct") is not None else None)),
        ("entry / stop / target / trail",
         _join(" / ", _money(_num(row, "entry")), _money(stop),
               _money(target), _money(trail))),
        ("stop distance", _pct(_num(row, "risk_pct"), 2, signed=False) + " of price"
         if _num(row, "risk_pct") is not None else None),
        ("reward : risk", (_ratio(_num(row, "reward_risk"), 1) + " R")
         if _num(row, "reward_risk") is not None else None),
    ])

    flags = _active_flags(row)
    out.append("")
    out.append(f"  Active setup flags   {', '.join(flags) if flags else 'none'}")

    # factor legs, when the screen carries them
    fcols = []
    try:
        fcols = [c for c in row.index if str(c).startswith("f_")]  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        fcols = []
    if fcols:
        legs = []
        for c in fcols:
            v = _num(row, c)
            if v is not None:
                legs.append(f"{str(c).replace('f_', '').replace('_', ' ')} {v:+.2f}")
        if legs:
            out.append("")
            out.append("  Factor legs (cross-sectional sigma; positive = supportive)")
            for i in range(0, len(legs), 3):
                out.append("    " + " | ".join(legs[i:i + 3]))
    return out


# ==========================================================================
# the prompts
# ==========================================================================
def build_prompt(row: Any, as_of: Optional[str] = None,
                 source: Optional[str] = None,
                 horizon: str = DEFAULT_HORIZON,
                 include_snapshot: bool = True) -> str:
    """Full research prompt for one screen row. Returns plain text."""
    ticker = _s(row, "ticker", "?")
    name = _s(row, "name", "the company")
    sector = _s(row, "sector", "its sector")
    side = _side_word(row)
    data_date = as_of or _s(row, "last_date") or "the latest session"
    direction = "higher" if side == "long" else "lower"
    move = _move_phrase(row)
    close = _num(row, "close")
    price_word = (f"the {_money(close)} in my snapshot" if close is not None
                  else "my snapshot's price")

    lines: List[str] = []
    add = lines.append

    add(f"I want a deep, evidence-based research note on {ticker} ({name}, {sector}).")
    add("")
    add("CONTEXT")
    add("I run a purely TECHNICAL screen over ~2,900 US stocks - price and volume history")
    add("only, no fundamentals, no estimates, no news. It currently classifies "
        f"{ticker} as a")
    add(f"{side.upper()} candidate ({_score_sentence(row)}), after a move of {move}.")
    add("The screen tells me what the tape is doing. It cannot tell me why, and it cannot")
    add("tell me whether the move is justified. That is what I need from you.")
    add("")
    add("THE TWO QUESTIONS - AND THE CALL I NEED")
    add(f"1. What was the fundamental story behind {ticker}'s share price move - what")
    add("   actually happened at the company and around it, and how much of the move can")
    add("   each event explain?")
    add(f"2. Is the move likely to CONTINUE {direction} over {horizon}, and why? Give me")
    add("   your reasoning, the evidence behind it, and the conditions under which it")
    add("   would fail.")
    add(f"3. Then commit. Tell me which way you actually expect {ticker} to go over "
        f"{horizon},")
    add("   with a probability, a price target, and the one to three reasons why.")
    add("")

    if include_snapshot:
        add(f"MY MODEL'S SNAPSHOT OF {ticker} (technical only)")
        add("")
        lines.extend(snapshot_lines(row, as_of=as_of))
        add("")
        if source:
            add(f"Source: {source}")
            add("")

    add("HOW TO ANSWER")
    add("")
    add("1) THE FUNDAMENTAL STORY SO FAR")
    add("   - Walk the last 12 months chronologically. For each material move, name the")
    add("     driver and the date, and say roughly how big the price reaction was.")
    add("   - Separate FUNDAMENTAL drivers (revenue/EPS growth, margin or guidance")
    add("     changes, product cycle, contract wins or losses, pricing power, capacity,")
    add("     regulation, litigation, management change, M&A, capital raises, buybacks,")
    add("     debt) from NON-FUNDAMENTAL ones (index inclusion or flows, positioning and")
    add("     short covering, sector or style rotation, multiple re-rating, retail or")
    add("     meme flows, macro rates/FX/commodity moves). Say which mattered more.")
    add("   - Quantify wherever you can: the actual reported numbers and guidance,")
    add("     consensus estimates before and after, and the resulting estimate")
    add("     revisions, not adjectives.")
    add("")
    add("2) THE KPI TREND, AND HOW IT COMPARES WITH ITS PEERS")
    add("   - Pull the handful of KPIs that actually drive this business and its valuation:")
    add("     revenue, gross and operating margin, EPS, free cash flow, plus the")
    add("     industry-specific ones that matter here (subscribers, ARR / bookings /")
    add("     backlog, unit volumes, same-store or same-clinic sales, net adds, churn,")
    add("     utilisation, pricing, guidance). Quarterly, for the last 8-12 quarters, or")
    add("     as far back as they are reported - say how far back the data goes.")
    add("   - Show me the TREND, not just the latest level. Give the series as a table I can")
    add("     chart, one row per KPI and one column per quarter, with YoY and QoQ. If you")
    add("     can render a chart or run code, chart it. Then say in words what the shape is:")
    add("     accelerating, steady, or decelerating over the last two or three quarters.")
    add("   - Do the same for the 2-4 closest competitors or the peer group, on the SAME")
    add("     KPIs over the SAME quarters, and put them side by side with this company so")
    add("     both the LEVELS and the RATES OF CHANGE can be compared. Is the gap opening")
    add("     or closing, and is this company gaining or losing share?")
    add("   - Then the part that matters to me: is that KPI trend consistent with the share")
    add("     price move, or has the market run ahead of the numbers (or fallen behind")
    add("     them)? Tie it back to the re-rating, if there has been one.")
    add("   - If a KPI is not available, say so, use the closest substitute you can find,")
    add("     and mark it as an approximation. Do not invent quarterly numbers.")
    add("")
    add("3) WHAT THE MARKET IS PRICING NOW")
    add("   - Where do valuation multiples (P/E, EV/EBITDA, FCF yield - whichever fits")
    add("     the business) sit versus this company's own 3-5 year history and versus its")
    add("     closest peers? Has the re-rating been driven by earnings or by the multiple?")
    add("   - Are estimates still being revised up or down? Any recent guidance cuts or")
    add("     beats? Any analyst target changes that cluster around a date?")
    add("   - Is there evidence the move was flow-driven rather than information-driven")
    add("     (volume patterns, short interest, ownership changes, index events)?")
    add("   - State plainly what is ALREADY priced in, so I can see what is left.")
    add("")
    add(f"4) THE CALL FOR {horizon.upper()} - COMMIT TO ONE")
    add("   - Decide, and put it in one line in exactly this shape:")
    add("       CALL: <HIGHER or LOWER> - <probability>% - target $<price>")
    add(f"             (<+/-x>% from {price_word}) by <date in the window>")
    add("     One direction, one probability, one price target, one date. No hedging in that")
    add("     line - not 'it depends', not a range instead of a call, not both cases.")
    add("   - Bull or bear: pick the one you actually expect to happen, not the one that is")
    add("     easiest to argue. Give your honest single-number probability, and keep it")
    add("     consistent with what you wrote in 1-3 above. A 50% call says you have no edge.")
    add("   - Give 1-3 reasons why - at least one, and never more than three. One plain")
    add("     sentence each, no jargon, each one a fact or a mechanism rather than a")
    add("     feeling. If you need a fourth reason, the thesis is not simple enough: cut it.")
    add("   - Then list the scheduled catalysts in that window with DATES where they are")
    add("     known: earnings, guidance events, investor days, product launches, regulatory")
    add("     or trial decisions, contract or budget decisions, index rebalances, lock-up")
    add("     expiries, debt maturities, and the macro prints that actually matter to this")
    add("     business. Flag any date that is estimated rather than confirmed.")
    add("   - Then give the base, bull and bear scenarios with the mechanism that gets you")
    add("     to each one (not just a direction) and a rough probability for each, summing")
    add("     to 100%. Your call above must be the highest-probability one of the three.")
    add("   - Say whether the technical levels in the snapshot above are a sensible way")
    add("     to hold the position given the fundamental risk you have identified,")
    add("     especially event and gap risk around the dates above.")
    add("   - If the evidence genuinely cannot support a direction after all that, say so")
    add("     once - and still give the one line with your best probability and target.")
    add("")
    add("5) WHAT WOULD PROVE YOU WRONG")
    add("   - The specific facts, disclosures or price behaviour that would invalidate your")
    add("     view. Be concrete: an actual number, an actual event, an actual level.")
    add("")
    add("6) MONITORING LIST")
    add("   - 3-5 things I should check weekly over the next quarter, each with the number")
    add("     or level that would change your mind. Include the KPI to watch next quarter.")
    add("")
    add("OUTPUT FORMAT")
    add("   - Start with the one-line CALL at the very top - nothing above it, not even a")
    add("     greeting - then a 3-sentence BOTTOM LINE, then the sections above.")
    add("   - Use markdown headings and bullets, and give me these four things: the one-line")
    add("     call; one table of the KPIs by quarter versus peers, with YoY and QoQ; one")
    add("     compact table of dated catalysts; one table of the bull / base / bear cases.")
    add("     If you can chart the KPI series, chart it as well as tabulating it.")
    add("   - Roughly 1,000-1,500 words. Dense with specifics; skip boilerplate.")
    add("")
    add("SOURCES AND HONESTY")
    add("   - If you can browse the web, do it, and cite the sources you use with dates.")
    add("     Prefer filings, transcripts, and reporting from the last 90 days.")
    add("   - If you cannot browse, say so up front, state your knowledge cut-off, and")
    add("     label every claim that needs checking against live data.")
    add("   - Where you are unsure, say so rather than guessing, and mark anything you")
    add("     could not verify. Do not give me generic investing education, and do not")
    add("     simply restate the snapshot above back to me.")
    add("")
    add(f"Assume today is on or shortly after {data_date}. This is research to inform my")
    add("own decision, not a recommendation to trade.")
    return "\n".join(lines)


def build_short_prompt(row: Any, as_of: Optional[str] = None) -> str:
    """A compact version of the prompt for a quick chat."""
    ticker = _s(row, "ticker", "?")
    name = _s(row, "name", "the company")
    sector = _s(row, "sector", "its sector")
    side = _side_word(row)
    direction = "higher" if side == "long" else "lower"
    data_date = as_of or _s(row, "last_date") or "the latest session"

    bits = [f"{ticker} ({name}, {sector}) is {_move_phrase(row)}."]
    detail = []
    if _num(row, "close") is not None:
        detail.append(f"price {_money(_num(row, 'close'))}")
    for key, lbl in (("rsi14", "RSI(14)"), ("adx14", "ADX(14)")):
        if _num(row, key) is not None:
            detail.append(f"{lbl} {_ratio(_num(row, key), 1)}")
    if _num(row, "atr_pct") is not None:
        detail.append(f"ATR {_pct(_num(row, 'atr_pct'), 1, signed=False)} of price")
    if _num(row, "range_pos_52w") is not None:
        detail.append(f"{_num(row, 'range_pos_52w'):.0f}% up its 52-week range")
    if _num(row, "avg_dollar_vol_m") is not None:
        detail.append(f"${_num(row, 'avg_dollar_vol_m'):,.0f}M/day traded")
    if detail:
        bits.append("Latest: " + ", ".join(detail) + f" (as of {data_date}).")
    flags = _active_flags(row)
    if flags:
        bits.append("Technical flags currently on: " + ", ".join(flags) + ".")
    bits.append(f"My pure-technical screen tags it a {side.upper()} candidate "
                f"({_score_sentence(row)}).")
    bits.append("")
    bits.append(f"My screen's bet is that the move continues {direction}; tell me what you "
                "think. Answer three things:")
    bits.append("1) The fundamental story behind this move - what actually happened at the "
                "company, by date, and how much of the move each event explains.")
    bits.append("2) How the KPIs that drive this business have trended over the last few "
                "quarters (revenue, margin, EPS, cash flow and the industry-specific "
                "ones), with the same KPIs for its closest competitors, so the levels and "
                "the rates of change can be compared - and whether that trend supports "
                "the share price move.")
    bits.append("3) Which way it actually goes over the next 1-3 months: commit to higher "
                "or lower, with a probability, a price target, and 1-3 plain reasons why - "
                "plus the dated catalysts in that window and what would prove you wrong.")
    bits.append("Use web search if you have it and cite sources with dates; if not, say "
                "so and flag what needs checking. Be specific and quantitative, not "
                "generic. Around 500 words.")
    return "\n".join(bits)


def prompts_frame(df: pd.DataFrame, as_of: Optional[str] = None,
                  source: Optional[str] = None,
                  style: str = "full",
                  limit: Optional[int] = None,
                  ticker_col: str = "ticker") -> pd.DataFrame:
    """One row per ticker with its prompt, for CSV download.

    `style` is "full" or "short".  The prompt is the last column so the frame
    stays readable in a spreadsheet.
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["ticker", "name", "sector", "side", "score",
                                     "research_prompt"])
    d = df if limit is None else df.head(int(limit))
    builder = build_short_prompt if style == "short" else build_prompt
    rows = []
    for _, r in d.iterrows():
        prompt = (builder(r, as_of=as_of) if style == "short"
                  else builder(r, as_of=as_of, source=source))
        rows.append({
            "ticker": _s(r, ticker_col),
            "name": _s(r, "name"),
            "sector": _s(r, "sector"),
            "side": _s(r, "side"),
            "score": _num(r, "score"),
            "research_prompt": prompt,
        })
    return pd.DataFrame(rows)


def prompt_filename(ticker: str, as_of: Optional[str] = None,
                    style: str = "full") -> str:
    """Sensible download filename, e.g. `AAPL_research_prompt_2026-10-09.txt`."""
    t = "".join(ch for ch in str(ticker or "ticker") if ch.isalnum() or ch in "-_.")
    parts = [t or "ticker", "short_prompt" if style == "short" else "research_prompt"]
    if as_of:
        parts.append(str(as_of))
    return "_".join(parts) + ".txt"
