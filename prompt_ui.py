"""
Shared Streamlit widgets for the per-ticker research prompts.

Both front ends (`app.py`, the pipeline dashboard, and `dashboard.py`, the CSV
explorer) show the same thing in three places:

  * the ranked table itself, with a one-click 📋 copy button on every row
  * on the ticker-detail view
  * in a dedicated Research tab, with a batch CSV export

so the presentation lives here once.  The prompt text itself is built by
`research_prompt.py`, which has no Streamlit dependency at all.

Two ways to get a prompt onto the clipboard:

  * `copy_table` — the single ranked grid, with a real 📋 button on every row.
    `st.dataframe` cannot host a button inside a cell, so the rows are rendered
    as HTML in a component; Streamlit's component iframe carries
    `allow-scripts allow-same-origin clipboard-write`, which is what makes
    `navigator.clipboard` work and lets the table inherit the app's theme.
    The column set, headers and number formats come from the same user-picked
    column list the grid used to take, via `infer_columns`.
  * `prompt_panel` — `st.code` renders a copy icon at its top-right, plus a
    .txt download, for reading a prompt in full.
"""

from __future__ import annotations

import html as _html
import json
import re
from typing import Any, List, Optional, Sequence, Tuple

import streamlit as st
import streamlit.components.v1 as components

from research_prompt import (build_prompt, build_short_prompt,  # noqa: F401
                              prompt_filename, prompts_frame)

PROMPT_STYLES = {
    "full": "Full research brief",
    "short": "Short prompt (~400 words)",
}

__all__ = ["PROMPT_STYLES", "DEFAULT_HINT", "prompt_text", "prompt_panel",
           "copy_table", "infer_columns", "rank_first", "ranks_first",
           "read_copy_table",
           "selected_rows",
           "prompts_frame", "build_prompt", "build_short_prompt",
           "prompt_filename"]


def prompt_text(row: Any, style: str = "full", as_of: Optional[str] = None,
                source: Optional[str] = None) -> str:
    """Full or short research prompt for one screened row."""
    if style == "short":
        return build_short_prompt(row, as_of=as_of)
    return build_prompt(row, as_of=as_of, source=source)


def prompt_panel(row: Any, *, key: str, style: str = "full",
                 as_of: Optional[str] = None, source: Optional[str] = None,
                 title: Optional[str] = None) -> str:
    """Render the copy-paste block for one row. Returns the prompt text."""
    ticker = str(row.get("ticker", "?"))
    text = prompt_text(row, style, as_of=as_of, source=source)
    heading = title or f"### 💬 Research prompt — {ticker}"
    st.markdown(
        f"{heading}  \n"
        f"<span style='color:#8a8a8a;font-size:0.8rem'>"
        f"{PROMPT_STYLES.get(style, style)} · {len(text):,} characters"
        f"</span>",
        unsafe_allow_html=True,
    )
    st.code(text, language=None, wrap_lines=True)
    c1, c2 = st.columns([1, 3])
    c1.download_button("⬇ Download .txt", text.encode("utf-8"),
                       file_name=prompt_filename(ticker, as_of, style),
                       mime="text/plain", use_container_width=True,
                       key=f"{key}_dl")
    c2.caption("**To use:** click the ⧉ copy icon at the top-right of the box, then "
               "paste it into ChatGPT / Claude / Gemini / Perplexity. The prompt "
               "carries the ticker's own numbers with it, so it needs no other "
               "context. Press ⬇ to keep a copy as a .txt file.")
    return text


def selected_rows(state: Any) -> List[int]:
    """Row positions from an `on_select` dataframe, across Streamlit versions."""
    if not state:
        return []
    for getter in (lambda s: s.selection.rows, lambda s: s["selection"]["rows"]):
        try:
            return [int(i) for i in getter(state)]
        except Exception:  # noqa: BLE001
            continue
    return []


# ==========================================================================
# the table — one grid, with a 📋 copy button on every row
# ==========================================================================
# Friendly headers for the columns whose raw names are cryptic. Anything not
# listed here gets its raw name with underscores turned into spaces.
_HEADERS = {
    "view #": "#", "rank": "#", "rank_long": "Rank L", "rank_short": "Rank S",
    "rank_long_adj": "Rank L (regime)", "rank_short_adj": "Rank S (regime)",
    "ticker": "Ticker", "name": "Company", "sector": "Sector", "side": "Side",
    "universe": "Index", "universes": "Indexes", "flags": "Setup flags",
    "last_date": "Last date", "bars": "Bars", "stale_days": "Stale d",
    "close": "Price", "prev_close": "Prev close", "open": "Open", "high": "High",
    "low": "Low",
    "chg_1d_pct": "1d %", "ret_1m": "1m %", "ret_3m": "3m %", "ret_6m": "6m %",
    "ret_12m": "12m %", "ret_12_1": "12-1 %",
    "rs_ret_1m": "RS 1m", "rs_ret_3m": "RS 3m", "rs_ret_6m": "RS 6m",
    "rsi14": "RSI(14)", "rsi2": "RSI(2)", "stoch_k": "Stoch %K",
    "williams_r": "Williams %R", "mfi14": "MFI(14)",
    "adx14": "ADX(14)", "adx_signed": "ADX signed", "plus_di": "+DI",
    "minus_di": "−DI", "di_spread": "+DI −DI",
    "atr14": "ATR(14)", "atr_pct": "ATR %",
    "hi_52w": "52w high", "lo_52w": "52w low", "range_pos_52w": "52w pos",
    "pct_to_52w_high": "to 52wH", "pct_above_52w_low": "off 52wL",
    "avg_dollar_vol_m": "$vol M", "avg_dollar_vol_20": "$vol 20d",
    "avg_dollar_vol_60": "$vol 60d", "rvol": "RVOL", "cmf_21": "CMF(21)",
    "obv_slope_63": "OBV slope", "ad_line_slope": "A/D slope",
    "vwap20_dist_pct": "vs VWAP20", "rolling_vwap20": "VWAP20",
    "vol_21": "vol 21d", "vol_63": "vol 63d", "vol_pctile": "vol pctile",
    "bbw": "BB width", "bbw_pctile": "BBW pctile", "pct_b": "%B",
    "zscore_20": "z(20)", "stretch_atr_20": "stretch 20d",
    "stretch_atr_50": "stretch 50d", "stretch_atr_200": "stretch 200d",
    "up_day_ratio_63": "up days", "mom3_over_vol": "3m/vol",
    "mom6_over_vol": "6m/vol", "mom12_1_over_vol": "12-1/vol",
    "macd_hist_pct": "MACD hist", "ma_stack": "MA stack",
    "sma20": "SMA20", "sma50": "SMA50", "sma200": "SMA200", "ema21": "EMA21",
    "dist_sma20_pct": "vs SMA20", "dist_sma50_pct": "vs SMA50",
    "dist_sma200_pct": "vs SMA200", "dist_ema21_pct": "vs EMA21",
    "sma50_slope": "SMA50 slope", "sma200_slope": "SMA200 slope",
    "entry": "Entry", "stop": "Stop", "target": "Target", "trail": "Trail",
    "stop_long": "Stop L", "stop_short": "Stop S", "target_long": "Target L",
    "target_short": "Target S", "trail_long": "Trail L", "trail_short": "Trail S",
    "risk_pct": "Risk %", "reward_risk": "R:R",
    "shares_for_1pct_risk": "Shares/1%", "long_score": "Long score",
    "short_score": "Short score", "long_score_adj": "Long score (regime)",
    "short_score_adj": "Short score (regime)", "long_pct": "Long pctile",
    "short_pct": "Short pctile", "long_disp": "Score", "score": "Score",
    "n_flags": "Flags", "eligible": "Eligible", "is_optionable": "Optionable",
    "in_SP500": "S&P 500", "in_SP400": "S&P 400", "in_SP600": "S&P 600",
    "in_RUSSELL2000": "Russell 2000", "in_RUSSELL1000": "Russell 1000",
}

# Column → how to render it.  Everything here is drawn from the CSV's own
# units — nothing is rescaled, so the table agrees with the exported file.
_MONEY = {"close", "prev_close", "open", "high", "low", "hi_52w", "lo_52w",
          "sma20", "sma50", "sma200", "ema21", "atr14", "rolling_vwap20",
          "entry", "stop", "target", "trail", "stop_long", "stop_short",
          "target_long", "target_short", "trail_long", "trail_short"}
_INT = {"view #", "bars", "n_flags", "shares_for_1pct_risk", "stale_days"}
_BOOL = {"eligible", "is_optionable", "above_sma20", "above_sma50",
         "above_sma200", "in_SP500", "in_SP400", "in_SP600", "in_RUSSELL2000",
         "in_RUSSELL1000"}
_PCT1 = {"ret_1m", "ret_3m", "ret_6m", "ret_12m", "ret_12_1",
         "rs_ret_1m", "rs_ret_3m", "rs_ret_6m", "vol_21", "vol_63"}
_PCT2 = {"risk_pct", "pct_to_52w_high", "pct_above_52w_low"}
_NUM0 = {"range_pos_52w", "long_pct", "short_pct", "rank", "rank_long",
         "rank_short", "rank_long_adj", "rank_short_adj"}
_NUM1 = {"rsi14", "rsi2", "stoch_k", "williams_r", "mfi14", "adx14",
         "adx_signed", "plus_di", "minus_di", "di_spread", "reward_risk"}
_TEXT = {"ticker", "name", "sector", "side", "flags", "universe",
         "last_date", "ma_stack"}
# long free-text cells (multi-value fields) get truncated with a hover title
_WIDE = {"universes"}
# 0-1 fractions that must NOT be dressed up with a % sign
_FRAC = {"pct_b"}

# kinds, as emitted in the markup: <td class=... data-col=... data-v=...>
_KINDS = ("ticker", "text", "wide", "side", "flags", "bool", "money", "int",
          "num0", "num1", "num2", "num3", "pct1", "pct2")


def header_for(col: str) -> str:
    """Display name for a raw column."""
    c = str(col)
    if c in _HEADERS:
        return _HEADERS[c]
    if c.startswith("flag_"):
        return c[5:].replace("_", " ")
    return c.replace("_", " ")


def kind_for(col: str) -> str:
    """How to render a raw column: one of `_KINDS`."""
    c = str(col)
    if c == "ticker":
        return "ticker"
    if c == "side":
        return "side"
    if c == "flags":
        return "flags"
    if c in _BOOL or c.startswith("flag_") or c.startswith("in_"):
        return "bool"
    if c in _WIDE:
        return "wide"
    if c in _MONEY:
        return "money"
    if c in _INT:
        return "int"
    if c in _NUM0:
        return "num0"
    if c in _NUM1:
        return "num1"
    if c in _PCT1:
        return "pct1"
    if c in _FRAC:
        return "num2"
    if c in _PCT2 or c.endswith("_pct"):
        return "pct2"
    if c in _TEXT:
        return "text"
    # score / factor / oscillator-like leftovers
    if c.startswith(("long_score", "short_score", "f_", "score", "penalty_",
                     "long_disp")):
        return "num2"
    return "num2"


def infer_columns(rows: Any,
                  cols: Optional[Sequence[str]] = None) -> List[Tuple[str, str, str]]:
    """[(column, header, kind)] for `cols` (default: every column present)."""
    if rows is None or len(rows) == 0:
        return []
    first = rows.iloc[0] if hasattr(rows, "iloc") else rows[0]
    if cols is None:
        cols = list(first.index) if hasattr(first, "index") else list(first)
    out: List[Tuple[str, str, str]] = []
    for c in cols:
        c = str(c)
        try:
            present = c in first
        except TypeError:  # not a mapping — cannot check, keep it
            present = True
        if present:
            out.append((c, header_for(c), kind_for(c)))
    return out


def rank_first(cols: Sequence[str], rank_col: str = "score",
               anchor: str = "ticker") -> List[str]:
    """Move `rank_col` to sit immediately left of `anchor`.

    The ranking metric is what the row *means*, so it belongs next to the name
    rather than buried a dozen columns in.  No-op if either column is absent.
    """
    cols = list(cols)
    if rank_col not in cols or anchor not in cols:
        return cols
    cols.remove(rank_col)
    cols.insert(cols.index(anchor), rank_col)
    return cols


def ranks_first(cols: Sequence[str], anchor: str = "ticker",
                rank_col: str = "score") -> List[str]:
    """Lead the table with the model's own ranks: `Rank L | Rank S | Ticker`.

    `rank_long` / `rank_short` are what a row *is* — the screener's verdict on
    the name — so they take the slots immediately left of the ticker, where the
    eye lands first.  The view row number and the composite score, which used to
    sit there, move to the right-hand end of the row.

    Degrades to `rank_first()` when the model ranks are not in `cols` at all (a
    column set that omits them), so the score still reads next to the ticker.
    """
    cols = list(cols)
    leads = [c for c in ("rank_long", "rank_short") if c in cols]
    if not leads:
        return rank_first(cols, rank_col, anchor)
    moved = [c for c in ("view #", "rank", rank_col) if c in cols]
    body = [c for c in cols if c not in leads and c not in moved]
    at = body.index(anchor) if anchor in body else 0
    return body[:at] + leads + body[at:] + moved


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "1.0", "true", "yes", "y", "t")
    try:
        return bool(float(value))
    except (TypeError, ValueError):
        return bool(value)


def _cell(value: Any, kind: str) -> Tuple[str, str, str]:
    """(html, css class, machine-readable value) for one cell.

    The third element becomes `data-v`, so `read_copy_table` can turn the
    rendered markup back into a frame.
    """
    if value is None or (isinstance(value, float) and value != value) or \
            (isinstance(value, str) and not value.strip()):
        return "–", "dim", ""

    if kind == "bool":
        return ("✓", "ok", "1") if _truthy(value) else ("·", "off", "0")

    if kind in ("ticker", "text", "wide", "side", "flags"):
        raw = str(value)
        if kind == "ticker":
            return _html.escape(raw), "tk", _html.escape(raw, quote=True)
        if kind == "side":
            side = raw.upper()
            cls = ("side-l" if side.startswith("L")
                   else "side-s" if side.startswith("S") else "")
            return _html.escape(side), cls, _html.escape(side, quote=True)
        if kind in ("flags", "wide"):
            return _html.escape(raw), "ellip", _html.escape(raw, quote=True)
        return _html.escape(raw), "", _html.escape(raw, quote=True)

    try:
        num = float(value)
    except (TypeError, ValueError):
        raw = _html.escape(str(value), quote=True)
        return _html.escape(str(value)), "dim", raw

    if kind == "money":
        return f"${num:,.2f}", "num", repr(num)
    if kind == "int":
        return f"{num:,.0f}", "num", repr(int(round(num)))
    if kind == "num0":
        return f"{num:,.0f}", "num", repr(num)
    if kind == "num1":
        return f"{num:,.1f}", "num", repr(num)
    if kind == "num3":
        return f"{num:,.3f}", "num", repr(num)
    if kind == "pct1":
        return (f"{num:+.1f}%", "num pos" if num >= 0 else "num neg", repr(num))
    if kind == "pct2":
        return (f"{num:+.2f}%", "num pos" if num >= 0 else "num neg", repr(num))
    return f"{num:,.2f}", "num", repr(num)


def _copy_table_html(rows: Sequence[Any], prompts: Sequence[str],
                     columns: Sequence[Tuple[str, str, str]],
                     height: int = 560) -> str:
    """The table markup + the copy script, ready for a component iframe."""
    # the 📋 button is its own column, pinned to the far left
    head = "<th class='ctr cop'>Prompt</th>"
    head += "".join(
        "<th{cls} data-col='{col}' data-kind='{kind}'>{hdr}</th>".format(
            cls=" class='num'" if kind in ("money", "int", "num0", "num1",
                                           "num2", "num3", "pct1", "pct2")
            else " class='ctr'" if kind == "bool" else "",
            col=_html.escape(col, quote=True), kind=kind,
            hdr=_html.escape(header))
        for col, header, kind in columns)

    body = []
    for i, row in enumerate(rows):
        ticker = _html.escape(str(row.get("ticker", "") if hasattr(row, "get") else ""),
                              quote=True)
        cells = [f"<td class='ctr cop'><button class='copy' data-i='{i}' "
                 f"title='Copy the full research prompt for {ticker}'>"
                 f"📋 Copy</button></td>"]
        for col, _header, kind in columns:
            try:
                value = row.get(col)
            except AttributeError:
                value = None
            txt, cls, data_v = _cell(value, kind)
            tip = (f' title="{data_v}"' if cls == "ellip" and data_v else "")
            cells.append(f"<td class='{cls}' data-col='{_html.escape(col, quote=True)}'"
                         f" data-v=\"{data_v}\"{tip}>{txt}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")

    # json.dumps keeps quotes/newlines intact; the `<` swap stops a prompt from
    # ever closing the script tag early.
    blob = json.dumps(list(prompts)).replace("<", "\\u003c")
    wrap_h = max(140, height - 14)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"/>
<style>
  html, body {{ margin:0; padding:0; background:transparent; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
          font-size:12.5px; color:#31333f; }}
  .ct-wrap {{ max-height:{wrap_h}px; overflow:auto; border:1px solid rgba(128,128,128,.28);
              border-radius:8px; }}
  table {{ border-collapse:separate; border-spacing:0; width:100%; }}
  thead th.sortable {{ cursor:pointer; user-select:none; }}
  thead th.sortable:hover {{ color:#ff4b4b; }}
  thead th.asc::after {{ content:" ▲"; font-size:9px; }}
  thead th.desc::after {{ content:" ▼"; font-size:9px; }}
  thead th {{ position:sticky; top:0; z-index:2; text-align:left; padding:7px 9px;
              font-weight:600; font-size:11.5px; letter-spacing:.02em; text-transform:uppercase;
              background:var(--ct-bg, #ffffff); border-bottom:1px solid rgba(128,128,128,.35);
              white-space:nowrap; }}
  tbody td {{ padding:6px 9px; border-bottom:1px solid rgba(128,128,128,.16);
              white-space:nowrap; vertical-align:middle; }}
  tbody tr:hover td {{ background:rgba(128,128,128,.10); }}
  td.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  th.ctr, td.ctr {{ text-align:center; }}
  /* the copy column stays put while you scroll right through 140 columns */
  th.cop, td.cop {{ position:sticky; left:0; z-index:3;
                    background:var(--ct-bg, #ffffff);
                    box-shadow:1px 0 0 rgba(128,128,128,.25); }}
  thead th.cop {{ z-index:4; }}
  td.cop {{ padding:6px 7px; }}
  td.tk {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-weight:700; }}
  td.dim, td.ellip {{ color:rgba(128,128,128,.95); }}
  td.side-l {{ color:#26a69a; font-weight:700; }}
  td.side-s {{ color:#ef5350; font-weight:700; }}
  td.ellip {{ max-width:230px; overflow:hidden; text-overflow:ellipsis; }}
  td.pos {{ color:#26a69a; }}
  td.neg {{ color:#ef5350; }}
  td.ok {{ color:#26a69a; }}
  td.off {{ color:rgba(128,128,128,.55); }}
  button.copy {{ background:#ff4b4b; color:#fff; border:0; border-radius:6px;
                 padding:4px 11px; font-size:12px; font-weight:600; cursor:pointer;
                 white-space:nowrap; font-family:inherit; }}
  button.copy:hover {{ filter:brightness(1.08); }}
  button.copy.ok {{ background:#2e7d32; }}
  button.copy.bad {{ background:#757575; }}
</style></head>
<body>
<div class="ct-wrap"><table>
  <thead><tr>{head}</tr></thead>
  <tbody>{''.join(body)}</tbody>
</table></div>
<script>
(function () {{
  var PROMPTS = {blob};

  // inherit the app's own theme — the component iframe is same-origin
  try {{
    var pd = window.parent.document;
    var bodyStyle = getComputedStyle(pd.body);
    var shell = pd.querySelector('.stApp') || pd.body;
    var bg = getComputedStyle(shell).backgroundColor;
    if (bodyStyle.color) document.body.style.color = bodyStyle.color;
    if (bodyStyle.fontFamily) document.body.style.fontFamily = bodyStyle.fontFamily;
    document.documentElement.style.setProperty('--ct-bg', bg || 'transparent');
  }} catch (e) {{
    var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.body.style.color = dark ? '#fafafa' : '#31333f';
    document.documentElement.style.setProperty('--ct-bg', dark ? '#0e1117' : '#ffffff');
  }}

  function legacyCopy(text) {{
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '0';
    ta.style.left = '0';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    var done = false;
    try {{ done = document.execCommand('copy'); }} catch (err) {{ done = false; }}
    document.body.removeChild(ta);
    return done;
  }}

  function flash(btn, good) {{
    btn.textContent = good ? '✓ Copied' : '✗ Select & ⌘C';
    btn.classList.toggle('ok', good);
    btn.classList.toggle('bad', !good);
    window.setTimeout(function () {{
      btn.textContent = '📋 Copy';
      btn.classList.remove('ok', 'bad');
    }}, 1500);
  }}

  // click a header to sort this page, exactly like the grid used to
  var table = document.querySelector('table');
  var tbody = table.tBodies[0];
  var headers = table.tHead.rows[0].cells;
  var sortCol = -1;
  var sortDir = 1;

  function sortBy(ci, numeric) {{
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {{
      var va = a.cells[ci].getAttribute('data-v') || '';
      var vb = b.cells[ci].getAttribute('data-v') || '';
      if (numeric) {{
        var na = parseFloat(va);
        var nb = parseFloat(vb);
        if (isNaN(na)) na = -Infinity;
        if (isNaN(nb)) nb = -Infinity;
        return (na - nb) * sortDir;
      }}
      if (va === '') return 1;          // blanks always sink
      if (vb === '') return -1;
      return va.localeCompare(vb) * sortDir;
    }});
    for (var k = 0; k < rows.length; k++) tbody.appendChild(rows[k]);
  }}

  for (var h = 0; h < headers.length; h++) {{
    (function (ci) {{
      var th = headers[ci];
      var kind = th.getAttribute('data-kind');
      if (!kind) return;                // the prompt column is not sortable
      th.classList.add('sortable');
      th.addEventListener('click', function () {{
        sortDir = (sortCol === ci) ? -sortDir : 1;
        sortCol = ci;
        for (var k = 0; k < headers.length; k++) {{
          headers[k].classList.remove('asc', 'desc');
        }}
        th.classList.add(sortDir === 1 ? 'asc' : 'desc');
        sortBy(ci, th.classList.contains('num') || kind === 'bool');
      }});
    }})(h);
  }}

  var buttons = document.querySelectorAll('button.copy');
  for (var i = 0; i < buttons.length; i++) {{
    buttons[i].addEventListener('click', function (ev) {{
      var btn = ev.currentTarget;
      var text = PROMPTS[parseInt(btn.getAttribute('data-i'), 10)] || '';
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(
          function () {{ flash(btn, true); }},
          function () {{ flash(btn, legacyCopy(text)); }}
        );
      }} else {{
        flash(btn, legacyCopy(text));
      }}
    }});
  }}
}})();
</script>
</body></html>"""


DEFAULT_HINT = ("**📋 Copy** on any row puts that ticker's whole research prompt on "
                "the clipboard — one click, then paste it into ChatGPT / Claude / "
                "Gemini / Perplexity. Click a header to sort this page.")


def copy_table(rows: Any, *, cols: Optional[Sequence[str]] = None,
               style: str = "full", as_of: Optional[str] = None,
               source: Optional[str] = None, max_rows: int = 100,
               height: Optional[int] = None, hint: Optional[str] = None) -> int:
    """Render the ranked table: every row, with its own one-click 📋 button.

    `rows` is any DataFrame (or list of row-likes) already in display order —
    normally the page you are looking at — and `cols` the user's chosen column
    list.  `hint` is the caption printed above the table.  Returns the number of
    rows rendered.
    """
    if rows is None or len(rows) == 0:
        return 0

    frame = rows.head(int(max_rows)) if hasattr(rows, "head") else list(rows)[:max_rows]
    n = len(frame)
    records = ([r for _, r in frame.iterrows()] if hasattr(frame, "iterrows")
               else list(frame))
    columns = infer_columns(records, cols)
    prompts = [prompt_text(r, style, as_of=as_of, source=source) for r in records]

    text = DEFAULT_HINT if hint is None else hint
    if len(rows) > n:
        text += f" Showing the first {n:,} of {len(rows):,} rows."
    st.caption(text)

    if height is None:
        height = int(min(900, 92 + 33 * n))
    components.html(_copy_table_html(records, prompts, columns, height),
                    height=height, scrolling=False)
    return n


def read_copy_table(srcdoc: str):  # -> pandas.DataFrame
    """Inverse of what `copy_table` renders — the table, back as a frame.

    Reads `data-col` / `data-v` off the markup, so the headless tests can assert
    on exactly the table the browser shows (numeric columns come back numeric,
    flags come back boolean).
    """
    import pandas as pd  # local: the app itself needs pandas anyway, tests only

    body = srcdoc[srcdoc.find("<tbody>"):srcdoc.find("</tbody>")]
    records = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        rec = {}
        for col, val in re.findall(
                r"<td[^>]*data-col='([^']*)'[^>]*data-v=\"([^\"]*)\"", row_html):
            rec[_html.unescape(col)] = _html.unescape(val) or None
        if rec:
            records.append(rec)
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    for c in df.columns:
        s = df[c]
        if c in _BOOL or str(c).startswith("flag_"):
            df[c] = s.map(_truthy)
            continue
        if c in _TEXT:
            continue
        num = pd.to_numeric(s, errors="coerce")
        if s.notna().any() and num.notna().sum() == s.notna().sum():
            df[c] = num
    return df
