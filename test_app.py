"""
Headless test of the Streamlit dashboard using Streamlit's own AppTest runner.

This actually executes app.py end to end - every widget, every tab, the
filtering, the ranking and the drill-down - and fails if anything raises.

    python test_app.py
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

from prompt_ui import read_copy_table  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402


def check(at: AppTest, label: str) -> None:
    if at.exception:
        print(f"  FAIL  {label}")
        for e in at.exception:
            print("        ", e.value)
        raise SystemExit(1)
    print(f"  ok    {label}")


def table(at):
    """The ranked grid, parsed back out of the single copy-table component."""
    frames = at.get("iframe")
    if not frames:
        return None
    return read_copy_table(frames[0].proto.srcdoc)


def main() -> int:
    print("=" * 78)
    print("DASHBOARD TEST (headless AppTest)")
    print("=" * 78)

    at = AppTest.from_file(os.path.join(HERE, "app.py"), default_timeout=180)
    at.run()
    check(at, "initial render")

    print(f"    title: {at.title[0].value if at.title else '-'}")
    print(f"    tabs : {len(at.tabs)}")
    print(f"    metric/widget counts -> dataframes: {len(at.dataframe)}, "
          f"sliders: {len(at.slider)}, multiselects: {len(at.multiselect)}, "
          f"radios: {len(at.radio)}, selectboxes: {len(at.selectbox)}")

    warnings = [str(w.value) for w in at.warning]
    blocking = [w for w in warnings if "No screen found" in w]
    if not blocking and warnings:
        print("    non-blocking notices:")
        for w in warnings:
            print("        ", w)
    if blocking:
        for w in blocking:
            print("        ", w)
        print("\n  Run `python screener.py` first to test the populated dashboard.")
        return 0

    assert table(at) is not None, "no ranked table rendered"

    # --- exercise the price filter -------------------------------------
    price_sliders = [s for s in at.slider if "Price range" in str(s.label)]
    if price_sliders:
        s = price_sliders[0]
        lo, hi = s.value
        s.set_value((lo, min(hi, max(lo, 50.0))))
        at.run()
        check(at, f"price filter applied (<= {min(hi, max(lo, 50.0))})")
        print(f"    table rows now: {len(table(at))}")

    # --- exercise a flag filter ----------------------------------------
    flag_ms = [m for m in at.multiselect if "flags" in str(m.label).lower()]
    if flag_ms:
        flags = flag_ms[0].options
        print(f"    {len(flags)} optional flags available, e.g. {flags[:5]}")
        flag_ms[0].set_value([flags[0]])
        at.run()
        check(at, f"flag filter applied ({flags[0]})")
        print(f"    table rows now: {len(table(at))}")
        flag_ms[0].set_value([])
        at.run()

    # --- exercise the view switch --------------------------------------
    view_radios = [r for r in at.radio if "Show" in str(r.label)
                   or "Longs" in list(map(str, r.options))]
    if view_radios:
        for opt in view_radios[0].options:
            view_radios[0].set_value(opt)
            at.run()
            check(at, f"view = {opt}")

    # --- exercise the ranking metric -----------------------------------
    rank_sb = [s for s in at.selectbox if "Ranking metric" in str(s.label)]
    if rank_sb:
        sb = rank_sb[0]
        opts = list(map(str, sb.options))
        print(f"    {len(opts)} ranking metrics, e.g. {opts[:3]}")
        for opt in opts:
            sb.set_value(opt)
            at.run()
            check(at, f"rank by '{opt}'")

    # --- exercise the ticker drill-down ---------------------------------
    # exact match: the Research tab's picker is labelled "Ticker to research"
    tick_sb = [s for s in at.selectbox if str(s.label) == "Ticker"]
    if tick_sb:
        for t in list(map(str, tick_sb[0].options))[:4]:
            tick_sb[0].set_value(t)
            at.run()
            check(at, f"ticker detail: {t}")

    # --- exercise the per-ticker research prompts ------------------------
    print("\n    research prompts")
    codes = at.get("code")
    print(f"    {len(codes)} prompt block(s), "
          f"{len(at.get('download_button'))} download buttons")
    if codes and "fundamental story" in codes[0].value and "1-3 months" in codes[0].value \
            and "COMMIT TO ONE" in codes[0].value and "KPI" in codes[0].value:
        check(at, "copy-paste research prompt rendered for the default ticker")
    else:
        print("  FAIL  no research prompt rendered")
        return 1

    res_sb = [s for s in at.selectbox if str(s.label) == "Ticker to research"]
    if res_sb:
        picks = list(map(str, res_sb[0].options))[:3]
        # widget references go stale after every at.run() — re-fetch each time
        for t in picks:
            next(s for s in at.selectbox
                 if str(s.label) == "Ticker to research").set_value(t)
            at.run()
            check(at, f"research prompt: {t}")
        if picks and any(c.value.startswith(f"I want a deep, evidence-based "
                                            f"research note on {picks[-1]} ")
                         for c in at.get("code")):
            check(at, f"prompt text names the selected ticker ({picks[-1]})")
        else:
            print("  FAIL  prompt does not name the selected ticker")
            return 1

    # the ranked grid and the copy buttons are ONE table
    frames = at.get("iframe")
    if len(frames) != 1:
        print(f"  FAIL  expected exactly 1 table component, found {len(frames)}")
        return 1
    srcdoc = frames[0].proto.srcdoc
    n_btn = srcdoc.count("class='copy'")
    rows_shown = len(table(at))
    if n_btn == rows_shown and n_btn > 0:
        check(at, f"one table, with a 📋 button on each of its {n_btn} rows")
    else:
        print(f"  FAIL  copy buttons {n_btn} != table rows {rows_shown}")
        return 1
    # only *formatted ranked grids* count: the collapsed "every raw value"
    # expander in the pipeline app and the batch preview are different views
    first_th = re.search(r"<thead><tr>(<th[^>]*>)", srcdoc)
    order = list(table(at).columns)
    if first_th and "cop" in first_th.group(1):
        check(at, "the 📋 Copy button is the first column on the left")
    else:
        print("  FAIL  copy column is not first")
        return 1
    lead = [c for c in order if c in ("rank_long", "rank_short")]
    tail = [c for c in ("rank", "score") if c in order]
    if lead == ["rank_long", "rank_short"] and order[:2] == lead and \
            "ticker" in order and order.index("ticker") == 2 and \
            tail and order[-len(tail):] == tail:
        check(at, f"Rank L / Rank S lead the row, # and Score at the far right "
                  f"({order[:4]} … {order[-2:]})")
    else:
        print(f"  FAIL  model ranks are not leading the row: "
              f"{order[:6]} … {order[-3:]}")
        return 1

    dupes = [d for d in at.dataframe
             if "ticker" in list(getattr(d.value, "columns", []))
             and any(c in list(getattr(d.value, "columns", []))
                     for c in ("rank", "view #"))
             and len(list(getattr(d.value, "columns", []))) <= 40]
    if not dupes:
        check(at, "no duplicate ranked st.dataframe alongside it")
    else:
        print(f"  FAIL  {len(dupes)} duplicate ranked dataframe(s) still rendered")
        return 1

    # --- exercise custom weights ---------------------------------------
    cw = [c for c in at.checkbox if "custom weights" in str(c.label).lower()]
    if cw:
        cw[0].set_value(True)
        at.run()
        check(at, "custom factor weights enabled")

    # --- numeric sanity on the rendered table ---------------------------
    tbl = table(at)
    print(f"\n    rendered table: {tbl.shape[0]} rows x {tbl.shape[1]} cols")
    print(f"    columns: {list(tbl.columns)}")
    bad = tbl["close"].dropna()
    assert len(bad) == 0 or bad.min() > 0, "non-positive prices in output"
    print("\n" + "=" * 78)
    print("DASHBOARD TEST PASSED")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
