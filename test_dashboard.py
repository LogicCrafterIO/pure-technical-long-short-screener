"""
Headless test of the CSV visualisation dashboard (dashboard.py).

Two AppTest rules this file deliberately respects, because getting them wrong
produces false failures:

  1. Widget references go stale after every `at.run()` — always re-fetch.
  2. `multiselect.options` returns *formatted* labels, while `set_value`
     wants the underlying raw values.  So raw values are passed explicitly.

The rendered table is capped by "Rows to display", so filtering assertions read
the "Rows after filters" metric instead of counting rendered rows.

    python test_dashboard.py
"""

from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "vendor"))

import config as C  # noqa: E402
from prompt_ui import read_copy_table  # noqa: E402
from research_prompt import (build_prompt, build_short_prompt,  # noqa: E402
                              prompt_filename, prompts_frame)
from streamlit.testing.v1 import AppTest  # noqa: E402

FAILS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def check(at: AppTest, label: str) -> bool:
    if at.exception:
        for e in at.exception:
            fail(f"{label}: {e.value}")
        return False
    return True


# ---------------------------------------------------------------- accessors
def widget(seq, fragment: str):
    for w in seq:
        if fragment.lower() in str(getattr(w, "label", "")).lower():
            return w
    return None


def ms(at, frag):
    return widget(at.multiselect, frag)


def sb(at, frag):
    return widget(at.selectbox, frag)


def rd(at, frag):
    return widget(at.radio, frag)


def sl(at, frag):
    return widget(at.slider, frag)


def ti(at, frag):
    return widget(at.text_input, frag)


def click(at, frag) -> bool:
    """Click the button whose label best matches `frag`.

    Precision order matters: a plain substring match makes "All" hit
    "Reset all", which resets the very state the test is exercising.
    """
    btns = list(at.button)
    for pred in (lambda l: l == frag,
                 lambda l: l.lower() == frag.lower(),
                 lambda l: l.lower().startswith(frag.lower()),
                 lambda l: frag.lower() in l.lower()):
        for b in btns:
            if pred(str(b.label)):
                b.click()
                return True
    return False


def metric(at, label: str):
    for m in at.metric:
        if str(m.label).strip().lower() == label.lower():
            try:
                return float(str(m.value).replace(",", "").replace("%", ""))
            except ValueError:
                return None
    return None


def n_filtered(at):
    return metric(at, "Rows after filters")


def main_table(at: AppTest):
    """The ranked grid, parsed back out of the copy-table component.

    The grid is now a single HTML table (the rows carry a 📋 button), so there
    is no `st.dataframe` to read — `read_copy_table` inverts the markup.
    """
    frames = at.get("iframe")
    if not frames:
        return None
    return read_copy_table(frames[0].proto.srcdoc)


def copy_payload(at: AppTest):
    """The prompts the 📋 buttons will copy, in row order."""
    frames = at.get("iframe")
    if not frames:
        return []
    m = re.search(r"var PROMPTS = (\[.*?\]);\n", frames[0].proto.srcdoc, re.S)
    return json.loads(m.group(1).replace("\\u003c", "<")) if m else []


def main() -> int:
    print("=" * 78)
    print("CSV DASHBOARD TEST (headless AppTest)")
    print("=" * 78)

    csv = os.path.join(C.OUTPUT_DIR, "latest_screen.csv")
    all_cols = list(pd.read_csv(csv, nrows=0).columns)
    flag_cols = [c for c in all_cols if c.startswith("flag_")]
    sectors = sorted(pd.read_csv(csv, usecols=["sector"])["sector"].dropna().unique())

    at = AppTest.from_file(os.path.join(HERE, "dashboard.py"), default_timeout=300)
    at.run()
    if not check(at, "initial render"):
        return 1
    total = n_filtered(at)
    if not total:
        fail("no rows rendered on first paint")
        return 1
    ok(f"initial render — {total:,.0f} rows after default filters")
    print(f"    widgets -> sliders {len(at.slider)}, multiselect {len(at.multiselect)}, "
          f"radios {len(at.radio)}, selectboxes {len(at.selectbox)}, "
          f"buttons {len(at.button)}")

    # ================================================================ 1 BOOK
    print("\n[1] Book — long / short / both")
    r = rd(at, "Side")
    if r is None:
        fail("book radio missing")
    else:
        counts = {}
        for opt in ["Longs", "Shorts", "Both"]:
            rd(at, "Side").set_value(opt)
            at.run()
            if check(at, f"book = {opt}"):
                counts[opt] = n_filtered(at)
        print(f"    filtered rows -> {counts}")
        lg, sh, both = counts.get("Longs"), counts.get("Shorts"), counts.get("Both")
        if None in (lg, sh, both):
            fail("book options did not all render")
        elif lg and sh and abs((lg + sh) - both) <= 2 and lg != sh:
            ok(f"Longs + Shorts = Both ({lg:,.0f} + {sh:,.0f} = {both:,.0f})")
        else:
            fail(f"book partition wrong: {counts}")

        # every rendered row must match the selected book
        rd(at, "Side").set_value("Longs")
        at.run()
        t = main_table(at)
        if t is not None and len(t) and (t["side"] == "LONG").all():
            ok(f"book=Longs renders only LONG rows ({len(t)} shown)")
        else:
            fail("book=Longs rendered non-LONG rows")
        rd(at, "Side").set_value("Shorts")
        at.run()
        t = main_table(at)
        if t is not None and len(t) and (t["side"] == "SHORT").all():
            ok(f"book=Shorts renders only SHORT rows ({len(t)} shown)")
        else:
            fail("book=Shorts rendered non-SHORT rows")
        rd(at, "Side").set_value("Both")
        at.run()

    # ========================================================= 2 OPTIONABLE
    print("\n[2] Optionable")
    r = rd(at, "Optionable filter")
    if r is None:
        fail("optionable radio missing")
    else:
        seen = {}
        for v in ["All", "Optionable only", "Non-optionable only"]:
            rd(at, "Optionable filter").set_value(v)
            at.run()
            if check(at, f"optionable = {v}"):
                seen[v] = (n_filtered(at), metric(at, "Optionable"))
        print(f"    (filtered rows, optionable rows) -> {seen}")
        a, oa = seen.get("All", (None, None))
        on, oo = seen.get("Optionable only", (None, None))
        off, of = seen.get("Non-optionable only", (None, None))
        if None not in (a, on, off) and abs(on + off - a) <= 2:
            ok(f"partition is exact ({on:,.0f} + {off:,.0f} = {a:,.0f})")
        else:
            fail(f"optionable partition wrong: {seen}")
        if oo is not None and on is not None and abs(oo - on) <= 2:
            ok("'Optionable only' returns only optionable rows")
        else:
            fail(f"'Optionable only' mismatch: rows={on} optionable={oo}")
        if of == 0:
            ok("'Non-optionable only' returns zero optionable rows")
        else:
            fail(f"'Non-optionable only' has optionable={of}")
        rd(at, "Optionable filter").set_value("All")
        at.run()

    # ============================================================== 3 PRICE
    print("\n[3] Price")
    if sl(at, "Price range") is None:
        fail("price slider missing")
    else:
        full = n_filtered(at)
        sl(at, "Price range").set_value((5.0, 50.0))
        at.run()
        if check(at, "price slider = $5–$50"):
            tight = n_filtered(at)
            t = main_table(at)
            pmin = float(t["close"].min()) if t is not None and len(t) else None
            pmax = float(t["close"].max()) if t is not None and len(t) else None
            print(f"    filtered {full:,.0f} -> {tight:,.0f}; "
                  f"rendered price ${pmin:.2f} – ${pmax:.2f}")
            if tight < full and pmax <= 50.0 and pmin >= 5.0:
                ok("price filter narrows and respects both bounds")
            else:
                fail(f"price filter wrong: {full} -> {tight}, ${pmin}–${pmax}")
        if click(at, "≤ $25"):
            at.run()
            if check(at, "price band button"):
                t = main_table(at)
                if t is not None and len(t) and float(t["close"].max()) <= 25.0:
                    ok("price band button '≤ $25' applied")
                else:
                    fail("price band button did not apply")
        sl(at, "Price range").set_value((5.0, 500.0))
        at.run()

    # =========================================================== 4 SORTING
    print("\n[4] Sorting")
    if sb(at, "Sort by") is None:
        fail("sort selectbox missing")
    else:
        opts = list(map(str, sb(at, "Sort by").options))
        tested = 0
        for o in ["ret_6m", "rsi14", "adx14", "atr_pct", "avg_dollar_vol_m",
                  "long_score", "short_score", "rank_long"]:
            if o in opts:
                sb(at, "Sort by").set_value(o)
                at.run()
                if check(at, f"sort by {o}"):
                    tested += 1
        ok(f"{tested} sort columns exercised ({len(opts)-1} available)")

        for direction, good in (("Ascending", lambda v: v.iloc[0] <= v.iloc[-1]),
                                ("Descending", lambda v: v.iloc[0] >= v.iloc[-1])):
            sb(at, "Sort by").set_value("ret_6m")
            rd(at, "Direction").set_value(direction)
            at.run()
            t = main_table(at)
            v = t["ret_6m"].dropna() if t is not None else pd.Series(dtype=float)
            if len(v) > 2 and good(v):
                ok(f"{direction.lower()} verified on ret_6m "
                   f"({v.iloc[0]:.1f} … {v.iloc[-1]:.1f})")
            else:
                fail(f"{direction} order not respected")
        rd(at, "Direction").set_value("Descending")
        at.run()

    # ============================================================= 5 FLAGS
    print("\n[5] Optional flags")
    if sb(at, "Column set") is None:
        fail("column set selectbox missing")
    else:
        sb(at, "Column set").set_value("Flags only")
        at.run()
        check(at, "switched to Flags only column set")
    inc = ms(at, "Must have")
    if inc is None or not inc.options:
        fail("flag multiselect missing")
    else:
        print(f"    {len(flag_cols)} optional flags available")
        for f in flag_cols[:4]:
            ms(at, "Must have").set_value([f])
            at.run()
            t = main_table(at)
            if t is not None and f in t.columns:
                if bool(t[f].all()):
                    ok(f"require {f.replace('flag_','')} → all {len(t)} shown rows have it")
                else:
                    fail(f"require {f}: {int((~t[f]).sum())} shown rows lack it")
            else:
                fail(f"flag column {f} not rendered")
        ms(at, "Must have").set_value([])
        at.run()
        if n_filtered(at) != total:
            fail("clearing 'Must have' did not restore rows")
        else:
            ok("clearing the flag filter restores the row set")

        f0 = flag_cols[0]
        ms(at, "Must NOT have").set_value([f0])
        at.run()
        t = main_table(at)
        if t is not None and f0 in t.columns and not bool(t[f0].any()):
            ok(f"exclude {f0.replace('flag_','')} → no shown row has it")
        else:
            fail(f"exclude {f0} not enforced")
        ms(at, "Must NOT have").set_value([])
        at.run()

        a, b = flag_cols[0], flag_cols[1]
        ms(at, "Must have").set_value([a, b])
        rd(at, "Match").set_value("ALL (AND)")
        at.run()
        n_and = n_filtered(at)
        rd(at, "Match").set_value("ANY (OR)")
        at.run()
        n_or = n_filtered(at)
        print(f"    {a.replace('flag_','')} AND {b.replace('flag_','')}: "
              f"{n_and:,.0f} rows   vs   OR: {n_or:,.0f} rows")
        if n_and < n_or:
            ok("AND is stricter than OR")
        else:
            fail(f"AND/OR logic wrong: AND={n_and}, OR={n_or}")
        ms(at, "Must have").set_value([])
        rd(at, "Match").set_value("ALL (AND)")
        at.run()

    # ============================================================ 6 SEARCH
    print("\n[6] Search")
    sb(at, "Column set").set_value("Core (recommended)")
    at.run()
    if ti(at, "Ticker or company") is None:
        fail("search box missing")
    else:
        ti(at, "Ticker or company").set_value("AAPL")
        at.run()
        t = main_table(at)
        n = n_filtered(at)
        if t is not None and len(t) and set(t["ticker"]) == {"AAPL"}:
            ok(f"exact ticker search → {n:,.0f} row(s), ticker AAPL")
        else:
            fail(f"ticker search wrong (rows={n})")
        ti(at, "Ticker or company").set_value("bancorp")
        at.run()
        t = main_table(at)
        if t is not None and len(t) and t["name"].str.lower().str.contains(
                "bancorp", na=False).all():
            ok(f"company-name search → {n_filtered(at):,.0f} rows, all 'bancorp'")
        else:
            fail(f"name search wrong (rows={n_filtered(at)})")
        ti(at, "Ticker or company").set_value("")
        at.run()
        if n_filtered(at) == total:
            ok("clearing search restores the row set")

    # ====================================================== 7 INDEX/SECTOR
    print("\n[7] Index and sector")
    if ms(at, "Index membership") is None:
        fail("index multiselect missing")
    else:
        ms(at, "Index membership").set_value(["SP500"])
        at.run()
        n_sp = n_filtered(at)
        ms(at, "Index membership").set_value(["RUSSELL2000"])
        at.run()
        n_r2k = n_filtered(at)
        ms(at, "Index membership").set_value(["SP500", "SP400", "SP600", "RUSSELL2000"])
        at.run()
        print(f"    S&P 500 only: {n_sp:,.0f} rows   Russell 2000 only: {n_r2k:,.0f} rows")
        if n_sp and n_r2k and n_sp != n_r2k:
            ok("index membership filter works")
        else:
            fail(f"index filter wrong: SP500={n_sp}, R2K={n_r2k}")
    if ms(at, "Sector") is None:
        fail("sector multiselect missing")
    else:
        one = sectors[0]
        ms(at, "Sector").set_value([one])
        at.run()
        t = main_table(at)
        if t is not None and len(t) and set(t["sector"]) == {one}:
            ok(f"sector filter → only '{one}' ({n_filtered(at):,.0f} rows)")
        else:
            fail(f"sector filter wrong for '{one}'")
        ms(at, "Sector").set_value([])
        at.run()

    # =========================================================== 8 PRESETS
    print("\n[8] Presets")
    if sb(at, "Quick filter") is None or not any("Apply" in str(b.label)
                                                 for b in at.button):
        fail("preset controls missing")
    else:
        sb(at, "Quick filter").set_value("Momentum longs (uptrend, strong trend)")
        at.run()
        if click(at, "Apply"):
            at.run()
            if check(at, "apply preset"):
                t = main_table(at)
                n = n_filtered(at)
                alllong = t is not None and len(t) and (t["side"] == "LONG").all()
                print(f"    'Momentum longs' → {n:,.0f} rows, all LONG: {alllong}")
                if alllong:
                    ok("preset enforced book = Longs")
                else:
                    fail("preset did not enforce book = Longs")
        if click(at, "Reset all"):
            at.run()
            if check(at, "reset all"):
                n = n_filtered(at)
                if n and abs(n - total) <= 2:
                    ok(f"reset restores the original row set ({n:,.0f})")
                else:
                    fail(f"reset mismatch: {n:,.0f} vs {total:,.0f}")

    # ====================================================== 9 COLUMN SETS
    print("\n[9] Column sets")
    sets = list(map(str, sb(at, "Column set").options))
    for name in sets:
        sb(at, "Column set").set_value(name)
        at.run()
        check(at, f"column set '{name}'")
    sb(at, "Column set").set_value("Everything")
    at.run()
    t = main_table(at)
    n_all = t.shape[1] if t is not None else 0
    print(f"    'Everything' renders {n_all} of {len(all_cols)} columns "
          f"(+derived rank)")
    if n_all >= len(all_cols):
        ok("'Everything' exposes every CSV column")
    else:
        fail(f"'Everything' only rendered {n_all} of {len(all_cols)} columns")
    sb(at, "Column set").set_value("Core (recommended)")
    at.run()

    # ============================================================ 10 CHARTS
    print("\n[10] Charts")
    if sb(at, "X axis") is None:
        fail("scatter builder missing")
    else:
        sb(at, "X axis").set_value("atr_pct")
        sb(at, "Y axis").set_value("ret_6m")
        sb(at, "Colour by").set_value("sector")
        sb(at, "Bubble size").set_value("avg_dollar_vol_m")
        at.run()
        if check(at, "custom scatter"):
            ok("scatter builder renders (atr_pct vs ret_6m, coloured by sector)")
    if "sector" in [str(s) for s in at.selectbox]:
        pass
    # sector facet should have no near-duplicate buckets
    t = main_table(at)
    if t is not None:
        secs = set(t["sector"].dropna())
        if "Communication" in secs and "Communication Services" in secs:
            fail("sector facet still has duplicate 'Communication' buckets")
        else:
            ok("sector facet is normalised (no duplicate Communication buckets)")

    # ================================================= 11 COLUMN PICKER
    print("\n[11] Column visibility picker")
    if ms(at, "Columns to show") is None:
        fail("column picker multiselect missing")
    else:
        t = main_table(at)
        preset_n = t.shape[1] if t is not None else 0
        print(f"    preset 'Core (recommended)' renders {preset_n} columns")

        # deselect some columns -> the table must get narrower
        keep = ["ticker", "name", "side", "close", "ret_6m"]
        ms(at, "Columns to show").set_value(keep)
        at.run()
        if check(at, "set a 5-column selection"):
            t = main_table(at)
            got = [c for c in (t.columns if t is not None else []) if c != "view #"]
            if got == keep:
                ok(f"table narrowed to exactly {len(got)} columns: {got}")
            else:
                fail(f"column selection wrong: {got}")
            if preset_n and (t is not None and t.shape[1] < preset_n):
                ok(f"width shrank {preset_n} → {t.shape[1]} columns")

        # quick buttons
        if click(at, "− levels"):
            at.run()
            ok("clicked '− levels'")
        if click(at, "All"):
            at.run()
            t = main_table(at)
            if t is not None and t.shape[1] > preset_n:
                ok(f"'All' shows every column ({t.shape[1]} incl. view #)")
            else:
                fail("'All' did not show every column")
        if click(at, "− flags"):
            at.run()
            t = main_table(at)
            if t is not None and not any(c.startswith("flag_") for c in t.columns):
                ok("'− flags' removed every flag_* column")
            else:
                fail("'− flags' left flag columns")
        if click(at, "− factors"):
            at.run()
            t = main_table(at)
            if t is not None and not any(c.startswith("f_") for c in t.columns):
                ok("'− factors' removed every f_* column")
            else:
                fail("'− factors' left factor columns")
        if click(at, "Preset"):
            at.run()
            t = main_table(at)
            if t is not None and t.shape[1] == preset_n:
                ok(f"'Preset' restored the column set ({t.shape[1]} columns)")
            else:
                fail(f"'Preset' gave {None if t is None else t.shape[1]} "
                     f"columns, expected {preset_n}")

        # "None" -> warning, no table
        if click(at, "None"):
            at.run()
            if len(at.warning) >= 1:
                ok("'None' warns instead of rendering an empty table")
            else:
                fail("'None' did not warn")
        # selecting a different column set must reload the picker
        sb(at, "Column set").set_value("Momentum")
        at.run()
        t = main_table(at)
        if t is not None and "ret_12_1" in t.columns and "sector" not in t.columns:
            ok("switching column set reloads the picker")
        else:
            fail("switching column set did not reload the picker")
        sb(at, "Column set").set_value("Core (recommended)")
        at.run()

    # ===================================================== 12 PAGINATION
    print("\n[12] Pagination")
    if sb(at, "Rows per page") is None:
        fail("rows-per-page selectbox missing")
    else:
        n_tot = n_filtered(at)
        for size in [10, 25, 50]:
            sb(at, "Rows per page").set_value(size)
            at.run()
            if not check(at, f"rows per page = {size}"):
                continue
            t = main_table(at)
            v = t["view #"].tolist() if t is not None else []
            if v == list(range(1, min(size, int(n_tot)) + 1)):
                ok(f"page 1 with {size}/page shows rows 1–{len(v)}")
            else:
                fail(f"rows per page {size} → rows {v[:3]}…{v[-3:] if v else []}")

        sb(at, "Rows per page").set_value(25)
        at.run()
        expect_pages = int(-(-int(n_filtered(at)) // 25))
        print(f"    {n_filtered(at):,.0f} rows at 25/page = {expect_pages} pages")

        # last page holds the remainder
        if click(at, "⏭"):
            at.run()
            t = main_table(at)
            v = t["view #"].tolist() if t is not None else []
            n = int(n_filtered(at))
            last_start = v[0] if v else 0
            if v and v[-1] == n and len(v) <= 25:
                ok(f"last page ends at row {v[-1]:,} of {n:,} "
                   f"({len(v)} rows)")
            else:
                fail(f"last page wrong: {v[:2]}…{v[-2:] if v else []} of {n}")

        # previous / next navigation
        if click(at, "◀"):
            at.run()
            t = main_table(at)
            v = t["view #"].tolist() if t is not None else []
            # the page immediately before the last one must end where that one began
            if v and v[-1] == last_start - 1:
                ok(f"'◀' moved back one page (rows {v[0]:,}–{v[-1]:,})")
            else:
                fail(f"'◀' navigation wrong: {v[:1]}…{v[-1:]} "
                     f"(expected to end at {last_start - 1:,})")

        if click(at, "⏮"):
            at.run()
            t = main_table(at)
            v = t["view #"].tolist() if t is not None else []
            if v and v[0] == 1:
                ok("'⏮' returned to the first page")
            else:
                fail("'⏮' did not return to page 1")

        if click(at, "▶"):
            at.run()
            t = main_table(at)
            v = t["view #"].tolist() if t is not None else []
            if v and v[0] == 26:
                ok("'▶' advanced to page 2 (starts at row 26)")
            else:
                fail(f"'▶' navigation wrong: starts at {v[0] if v else None}")

        # go-to-page box
        at.number_input(key="page_no").set_value(3)
        at.run()
        t = main_table(at)
        v = t["view #"].tolist() if t is not None else []
        if v and v[0] == 51:
            ok("'Go to page' jump to page 3 (starts at row 51)")
        else:
            fail(f"page jump wrong: starts at {v[0] if v else None}")

        # changing page size resets to page 1
        sb(at, "Rows per page").set_value(10)
        at.run()
        t = main_table(at)
        v = t["view #"].tolist() if t is not None else []
        if v and v[0] == 1:
            ok("changing rows-per-page resets to page 1")
        else:
            fail("changing rows-per-page did not reset to page 1")

        # pagination must not change the underlying result count
        if int(n_filtered(at)) == n_tot:
            ok(f"pagination leaves the filtered set intact ({n_tot:,.0f} rows)")
        else:
            fail("pagination changed the filtered row count")
        sb(at, "Rows per page").set_value(50)
        at.run()

    # ===================================================== 13 RESEARCH PROMPTS
    print("\n[13] Research prompts — the copy-paste hand-off")
    # fresh slate, so row 1 of the table is the top of the book again
    if not click(at, "Reset all"):
        fail("'Reset all' button not found")
    at.run()
    sb(at, "Rows per page").set_value(50)
    at.run()
    t = main_table(at)
    top_ticker = str(t["ticker"].iloc[0]) if t is not None and len(t) else ""
    csv_name = os.path.basename(csv)
    raw = pd.read_csv(csv, dtype={"ticker": str})
    pick_row = lambda tk: raw[raw["ticker"] == tk].iloc[0]  # noqa: E731

    codes = at.get("code")
    if not top_ticker:
        fail("no ticker rendered, cannot test the research prompt")
    else:
        # -- the prompt in the Research tab must be the ticker's own numbers --
        expected = build_prompt(pick_row(top_ticker), as_of=None, source=csv_name)
        if len(codes) >= 2 and codes[0].value == expected:
            ok(f"Research tab quotes the exact prompt for the top row "
               f"{top_ticker} ({len(expected):,} chars)")
        else:
            fail(f"Research tab prompt does not match build_prompt for {top_ticker} "
                 f"({len(codes)} code blocks)")
        if len(codes) >= 2 and codes[1].value == expected:
            ok("Ticker detail tab shows the same prompt for its default ticker")
        else:
            fail("Ticker detail prompt differs from the Research tab's")

        # -- the prompt has to actually ask the two questions I want answered --
        must_have = [top_ticker, "THE TWO QUESTIONS", "fundamental story behind",
                     "CONTINUE", "1-3 months", "catalysts", "SOURCES AND HONESTY",
                     # the committed call: direction, probability, target, reasons
                     "COMMIT TO ONE", "CALL:", "probability", "price target",
                     "1-3 reasons", "never more than three",
                     # the KPI trend versus peers
                     "KPI", "peer", "quarter", "YoY", "QoQ", "RATES OF CHANGE",
                     "chart"]
        missing = [m for m in must_have if m not in expected]
        if not missing:
            ok("prompt covers the fundamental story, the 1-3 month call, dated "
               "catalysts and sourcing rules")
        else:
            fail(f"prompt is missing: {missing}")
        if "COMMIT TO ONE" in expected and "target $" in expected and \
                "highest-probability one of the three" in expected:
            ok("prompt forces one direction, a probability, a price target and 1-3 "
               "reasons - and makes the call the likeliest scenario")
        else:
            fail("prompt does not force a single committed call with a target")
        if "THE KPI TREND" in expected and "SAME quarters" in expected and \
                "side by side" in expected:
            ok("prompt asks for a quarterly KPI series versus peers on the same KPIs")
        else:
            fail("prompt does not ask for the KPI trend versus competitors")
        if str(t["close"].iloc[0])[:6] in expected.replace(",", "") or \
                f"{t['close'].iloc[0]:,.2f}" in expected:
            ok(f"prompt embeds the screen's own numbers (price "
               f"${t['close'].iloc[0]:,.2f})")
        else:
            fail("prompt does not carry the ticker's price")

    # -- a short-book name must be asked about the downside ------------------
    shorts = raw[raw["side"] == "SHORT"]
    if len(shorts):
        sp = build_prompt(shorts.iloc[0], as_of=None)
        if "CONTINUE lower" in sp:
            ok("a SHORT candidate is asked whether the move continues lower")
        else:
            fail("short-side prompt does not ask about downside continuation")

    # -- short style round-trips through the sidebar -------------------------
    # exact label, because "Prompt style" is a substring of "Batch prompt style"
    style_rd = next((w for w in at.radio if str(w.label) == "Prompt style"), None)
    if style_rd is None:
        fail("sidebar prompt-style radio missing")
    else:
        style_rd.set_value("short")
        at.run()
        codes = at.get("code")
        short_expected = build_short_prompt(pick_row(top_ticker), as_of=None)
        if len(codes) >= 1 and codes[0].value == short_expected:
            ok(f"short prompt style renders ({len(short_expected):,} chars vs "
               f"{len(expected):,} for the full brief)")
        else:
            fail("short prompt style did not render the compact prompt")
        next((w for w in at.radio if str(w.label) == "Prompt style"),
             style_rd).set_value("full")
        at.run()

    # -- the grid and the copy buttons are ONE table -------------------------
    # (they used to be a st.dataframe plus a copy-only HTML strip; now there is
    #  a single table, so nothing may duplicate the ticker list)
    dupes = []
    for i, d in enumerate(at.dataframe):
        try:
            cols = list(d.value.columns)
        except Exception:  # noqa: BLE001
            continue
        # the ranked grid is the one that numbers its rows; the batch preview in
        # the Research tab (5 cols) and the pipeline app's raw-values expander
        # (134 cols) are different views, not a second ranked grid
        if "ticker" in cols and ("view #" in cols or "rank" in cols) and len(cols) <= 40:
            dupes.append(i)
    if not dupes:
        ok("no second ranked grid: the copy table *is* the table")
    else:
        fail(f"a duplicate ranked st.dataframe is still rendered (index {dupes})")
    frames = at.get("iframe")
    if len(frames) == 1:
        ok("exactly one table on screen")
    else:
        fail(f"expected exactly 1 table component, found {len(frames)}")

    # -- layout: 📋 first, score next to the ticker ---------------------------
    if len(frames) == 1:
        srcdoc = frames[0].proto.srcdoc
        first_th = re.search(r"<thead><tr>(<th[^>]*>)", srcdoc)
        first_td = re.search(r"<tbody><tr>(<td[^>]*>)", srcdoc)
        if first_th and "cop" in first_th.group(1) and \
                first_td and "cop" in first_td.group(1) and \
                "class='copy'" in srcdoc:
            ok("the 📋 Copy button is the first column on the left")
        else:
            fail(f"copy column is not first: {first_th and first_th.group(1)}")
        order = list(t.columns) if t is not None else []
        lead = [c for c in order if c in ("rank_long", "rank_short")]
        tail = [c for c in ("view #", "score") if c in order]
        if lead == ["rank_long", "rank_short"] and order[:2] == lead and \
                "ticker" in order and order.index("ticker") == 2 and \
                tail and order[-len(tail):] == tail:
            ok(f"Rank L / Rank S lead the row, # and Score at the far right "
               f"({order[:4]} … {order[-2:]})")
        else:
            fail(f"model ranks are not leading the row: "
                 f"{order[:6]} … {order[-3:]}")

    # -- one-click copy buttons, inside the table ----------------------------
    if len(frames) == 1:
        srcdoc = frames[0].proto.srcdoc
        n_btn = srcdoc.count("class='copy'")
        shown = main_table(at)
        page_rows = len(shown) if shown is not None else 0
        if n_btn == page_rows and n_btn > 0:
            ok(f"every row on the page has its own 📋 Copy button "
               f"({n_btn} buttons for {page_rows} rows)")
        else:
            fail(f"copy buttons {n_btn} != page rows {page_rows}")
        payload = copy_payload(at)
        if payload:
            want = build_prompt(pick_row(top_ticker), as_of=None, source=csv_name)
            if payload and payload[0] == want:
                ok(f"the button's clipboard payload is the exact full prompt "
                   f"({len(want):,} chars)")
            else:
                fail("clipboard payload differs from build_prompt()")
            if len(payload) == n_btn and all(len(p) > 1000 for p in payload):
                ok("one full prompt per row, none empty")
            else:
                fail(f"prompts array wrong: {len(payload)} vs {n_btn} buttons")
        else:
            fail("no PROMPTS array embedded in the copy table")
        if "navigator.clipboard" in srcdoc and "execCommand" in srcdoc:
            ok("clipboard API with an execCommand fallback")
        else:
            fail("copy script missing a fallback path")
    else:
        fail(f"expected 1 copy-table iframe, found {len(frames)}")

    # -- batch export --------------------------------------------------------
    batch = prompts_frame(raw.head(3), source=csv_name, limit=3)
    if list(batch.columns)[-1] == "research_prompt" and len(batch) == 3 and \
            batch["research_prompt"].str.len().min() > 1000 and \
            batch["research_prompt"].nunique() == 3:
        ok("prompts_frame() → 3 distinct prompts, one per ticker")
    else:
        fail("prompts_frame() wrong shape or duplicate prompts")
    dl = [str(getattr(b, "label", "")) for b in at.get("download_button")]
    if any("prompt(s) as CSV" in lbl for lbl in dl):
        ok(f"batch prompt CSV is offered in the Research tab ({len(dl)} download "
           f"buttons on the page)")
    else:
        fail(f"no batch prompt download button: {dl}")
    if prompt_filename("BRK.B", "2026-10-09") == "BRK.B_research_prompt_2026-10-09.txt":
        ok("download filenames are ticker-safe")
    else:
        fail(f"prompt_filename() wrong: {prompt_filename('BRK.B', '2026-10-09')}")

    print("\n" + "=" * 78)
    if FAILS:
        print(f"FAILED {len(FAILS)} check(s):")
        for f in FAILS:
            print("   -", f)
        return 1
    print("CSV DASHBOARD TEST PASSED")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
