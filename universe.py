"""
Universe construction.

Pulls index constituents for four universes:

  * S&P 500   (SP500)        - Wikipedia constituent table (primary)
  * S&P 400   (SP400)        - Wikipedia constituent table (primary)
  * S&P 600   (SP600)        - Wikipedia constituent table (primary)
  * Russell 2000 (RUSSELL2000) - iShares IWM full holdings (primary)

Every universe has an independent fallback: if Wikipedia is unavailable the
S&P lists are rebuilt from the corresponding iShares Core ETF holdings
(IVV / IJH / IJR), and if a BlackRock endpoint fails we fall back to the last
good cached snapshot on disk.  The screener therefore still runs offline
against cache.

Output is a tidy DataFrame:  ticker | name | sector | universe | universes
"""

from __future__ import annotations

import html
import io
import json
import os
import re
import time
import urllib.request

import pandas as pd

import config as C

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_BLACKROCK_DOC = (
    "https://www.blackrock.com/varnish-api/blk-one01-product-data/"
    "product-data/api/v1/get-fund-document"
    "?appType=PRODUCT_PAGE&appSubType=ISHARES&targetSite=us-ishares"
    "&locale=en_US&portfolioId={pid}&component=fundDownload&userType=individual"
)

# Priority order used when a ticker appears in more than one index.
UNIVERSE_PRIORITY = ["SP500", "SP400", "SP600", "RUSSELL2000"]

# Wikipedia (GICS) and iShares spell a few sectors differently. Without this the
# sector facet splits into near-duplicate buckets, e.g. "Communication" (iShares)
# next to "Communication Services" (Wikipedia).
SECTOR_FIX = {
    "communication": "Communication Services",
    "communications": "Communication Services",
    "telecommunication services": "Communication Services",
    "telecom": "Communication Services",
    "information technology": "Information Technology",
    "technology": "Information Technology",
    "healthcare": "Health Care",
    "health care": "Health Care",
    "financial": "Financials",
    "finance": "Financials",
    "consumer discretionary": "Consumer Discretionary",
    "consumer staples": "Consumer Staples",
    "real estate": "Real Estate",
    "materials": "Materials",
    "industrials": "Industrials",
    "energy": "Energy",
    "utilities": "Utilities",
    "unknown": "Unknown",
}


def normalise_sector(s) -> str:
    """Map a source-specific sector label onto the standard GICS name."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return "Unknown"
    t = str(s).strip()
    if not t or t.lower() in ("nan", "none"):
        return "Unknown"
    return SECTOR_FIX.get(t.lower(), t)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def to_yf_symbol(raw) -> str:
    """Convert an index symbol to the yfinance/Yahoo convention.

    Wikipedia writes share classes as ``BRK.B`` whereas Yahoo uses ``BRK-B``.
    Anything that is not A-Z / 0-9 / '-' is stripped.
    """
    if raw is None:
        return ""
    s = str(raw).strip().upper()
    if not s or s in {"NAN", "-", "--", "N/A", "CASH", "USD"}:
        return ""
    s = s.replace(".", "-").replace("/", "-").replace(" ", "")
    s = re.sub(r"[^A-Z0-9\-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def _fetch(url: str, timeout: int = 45, referer: str | None = None) -> bytes:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------
# Source 1: Wikipedia S&P constituent tables
# --------------------------------------------------------------------------
def fetch_wikipedia_sp(label: str) -> pd.DataFrame:
    url = C.WIKI_SP[label]
    raw = _fetch(url)
    tables = pd.read_html(io.StringIO(raw.decode("utf-8", "replace")))
    for t in tables:
        cols = [str(c) for c in t.columns]
        hit = [c for c in cols if c in ("Symbol", "Ticker")]
        if not hit:
            continue
        sym_col = hit[0]
        name_col = next(
            (c for c in cols if c in ("Security", "Company", "Name")), None
        )
        sec_col = next(
            (c for c in cols if "GICS Sector" in c or c == "Sector"), None
        )
        out = pd.DataFrame()
        out["ticker"] = t[sym_col].map(to_yf_symbol)
        out["name"] = t[name_col].astype(str) if name_col else ""
        out["sector"] = t[sec_col].astype(str) if sec_col else "Unknown"
        out["universe"] = label
        out = out[out["ticker"] != ""].drop_duplicates("ticker")
        if len(out) < 50:
            continue
        return out.reset_index(drop=True)
    raise RuntimeError(f"no constituent table found for {label} at {url}")


# --------------------------------------------------------------------------
# Source 2: iShares / BlackRock full holdings (SpreadsheetML workbook)
# --------------------------------------------------------------------------
def fetch_ishares_holdings(label: str) -> pd.DataFrame:
    pid = C.ISHARES_FUNDS[label]["portfolio_id"]
    fund = C.ISHARES_FUNDS[label]["fund"]
    url = _BLACKROCK_DOC.format(pid=pid)
    raw = _fetch(url, timeout=90, referer="https://www.ishares.com/")
    doc = raw.decode("utf-8", "replace")

    m = re.search(r'<ss:Worksheet ss:Name="Holdings">(.*?)</ss:Worksheet>', doc, re.S)
    if not m:
        raise RuntimeError(f"Holdings sheet missing for {fund} (portfolioId={pid})")
    sheet = m.group(1)

    rows = []
    for rmatch in re.finditer(r"<ss:Row[^>]*>(.*?)</ss:Row>", sheet, re.S):
        cells = []
        for cm in re.finditer(r"<ss:Cell[^>]*>(.*?)</ss:Cell>", rmatch.group(1), re.S):
            dm = re.search(r"<ss:Data[^>]*>(.*?)</ss:Data>", cm.group(1), re.S)
            cells.append(
                html.unescape(re.sub(r"<[^>]+>", "", dm.group(1))).strip() if dm else ""
            )
        rows.append(cells)

    # locate the real header row (contains "Ticker" and "Weight")
    hdr_idx = None
    for i, r in enumerate(rows[:40]):
        joined = " ".join(r).lower()
        if "ticker" in joined and ("weight" in joined or "market value" in joined):
            hdr_idx = i
            break
    if hdr_idx is None:
        raise RuntimeError(f"could not locate holdings header for {fund}")

    header = [h.strip() for h in rows[hdr_idx]]
    body = [r for r in rows[hdr_idx + 1 :] if any(c for c in r)]
    width = len(header)
    body = [(r + [""] * width)[:width] for r in body]

    df = pd.DataFrame(body, columns=header)
    colmap = {c.lower(): c for c in df.columns}

    def pick(*names):
        for n in names:
            if n in colmap:
                return colmap[n]
        return None

    tk = pick("ticker")
    nm = pick("name")
    sc = pick("sector")
    ac = pick("asset class")
    if tk is None:
        raise RuntimeError(f"no ticker column for {fund}")

    out = pd.DataFrame()
    out["ticker"] = df[tk].map(to_yf_symbol)
    out["name"] = df[nm].astype(str) if nm else ""
    out["sector"] = df[sc].astype(str) if sc else "Unknown"

    if ac:
        is_eq = df[ac].astype(str).str.strip().str.lower().str.startswith("equity")
        out = out[is_eq]

    out["universe"] = label
    out = out[out["ticker"] != ""]
    # drop obvious non-equity artefacts
    out = out[~out["ticker"].isin({"USD", "CASH", "MMF"})]
    out = out.drop_duplicates("ticker")
    if len(out) < 100:
        raise RuntimeError(f"implausibly small holdings set for {fund}: {len(out)}")
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def _cache_path(label: str) -> str:
    return os.path.join(C.UNIVERSE_DIR, f"{label}.csv")


def _cache_fresh(path: str, days: int) -> bool:
    if not os.path.exists(path):
        return False
    age_days = (time.time() - os.path.getmtime(path)) / 86400.0
    return age_days < days


def _write_cache(df: pd.DataFrame, label: str, source: str) -> None:
    df = df.copy()
    df["source"] = source
    df["fetched_at"] = pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    df.to_csv(_cache_path(label), index=False)


def _read_cache(label: str) -> pd.DataFrame | None:
    path = _cache_path(label)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"ticker": str})
        df["ticker"] = df["ticker"].map(to_yf_symbol)
        return df[df["ticker"] != ""]
    except Exception:
        return None


def get_universe_members(label: str, force: bool = False, verbose: bool = True) -> pd.DataFrame:
    """Return constituents for one universe, using cache -> primary -> fallback."""
    path = _cache_path(label)
    if not force and _cache_fresh(path, C.UNIVERSE_CACHE_DAYS):
        df = _read_cache(label)
        if df is not None and len(df) >= 100:
            if verbose:
                print(f"  [{label}] cache hit: {len(df)} tickers")
            return df

    # primary source
    order = []
    if label in C.WIKI_SP:
        order.append(("wikipedia", lambda: fetch_wikipedia_sp(label)))
    order.append(("ishares:" + C.ISHARES_FUNDS[label]["fund"],
                  lambda: fetch_ishares_holdings(label)))
    if label not in C.WIKI_SP:
        order.append(("wikipedia", lambda: fetch_wikipedia_sp(label)))

    for source, fn in order:
        try:
            df = fn()
            _write_cache(df, label, source)
            if verbose:
                print(f"  [{label}] fetched {len(df)} tickers from {source}")
            return _read_cache(label)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  [{label}] source {source} failed: {type(e).__name__}: {e}")

    cached = _read_cache(label)
    if cached is not None and len(cached):
        if verbose:
            print(f"  [{label}] all live sources failed - using stale cache "
                  f"({len(cached)} tickers)")
        return cached
    raise RuntimeError(f"no way to build universe {label}")


def build_universe(labels=None, force: bool = False, verbose: bool = True) -> pd.DataFrame:
    """Build the combined, de-duplicated multi-index universe.

    Returns columns: ticker, name, sector, universe (primary), universes (all).
    """
    labels = list(labels) if labels else list(C.UNIVERSES.keys())
    frames = []
    for label in labels:
        try:
            frames.append(get_universe_members(label, force=force, verbose=verbose))
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] UNAVAILABLE: {e}")

    if not frames:
        raise RuntimeError("universe build produced no members")

    all_members = pd.concat(frames, ignore_index=True)
    all_members["ticker"] = all_members["ticker"].map(to_yf_symbol)
    all_members = all_members[all_members["ticker"] != ""]

    # membership map
    membership = (
        all_members.groupby("ticker")["universe"]
        .apply(lambda s: "|".join(sorted(set(s))))
        .rename("universes")
    )

    # pick primary universe by priority, and the richest name/sector metadata
    all_members["_prio"] = all_members["universe"].map(
        {u: i for i, u in enumerate(UNIVERSE_PRIORITY)}
    ).fillna(99)
    all_members = all_members.sort_values(["ticker", "_prio"])
    primary = all_members.drop_duplicates("ticker", keep="first").set_index("ticker")

    out = primary[["name", "sector", "universe"]].join(membership)
    out = out.reset_index()
    out["name"] = out["name"].astype(str).str.strip()
    out["sector"] = out["sector"].map(normalise_sector)
    out = out.sort_values("ticker").reset_index(drop=True)

    if verbose:
        print(f"\n  TOTAL UNIQUE TICKERS: {len(out)}")
        print(out["universe"].value_counts().to_string())
        multi = (out["universes"].str.contains(r"\|")).sum()
        print(f"  tickers in >1 index: {multi}")
    return out


def load_universe(force: bool = False, verbose: bool = True) -> pd.DataFrame:
    """Load the combined universe from the master cache, rebuilding if needed."""
    master = os.path.join(C.UNIVERSE_DIR, "universe.csv")
    if not force and _cache_fresh(master, C.UNIVERSE_CACHE_DAYS):
        try:
            df = pd.read_csv(master, dtype={"ticker": str})
            df["ticker"] = df["ticker"].map(to_yf_symbol)
            if len(df) > 100:
                if verbose:
                    print(f"  universe: master cache, {len(df)} tickers")
                return df
        except Exception:
            pass
    df = build_universe(force=force, verbose=verbose)
    df.to_csv(master, index=False)
    return df


if __name__ == "__main__":
    u = load_universe(force="--force" in os.sys.argv)
    print(u.head(15).to_string())
    path = os.path.join(C.UNIVERSE_DIR, "universe_meta.json")
    with open(path, "w") as fh:
        json.dump(
            {
                "built_at": pd.Timestamp.utcnow().isoformat(),
                "n_tickers": int(len(u)),
                "by_universe": u["universe"].value_counts().to_dict(),
            },
            fh,
            indent=2,
        )
    print("wrote", path)
