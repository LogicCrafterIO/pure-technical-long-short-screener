"""
Price data layer.

Downloads daily OHLCV for the whole universe from yfinance and caches it as
wide (date x ticker) parquet frames.

Two properties matter here:

* **Incremental** - on a re-run only tickers missing from the cache are
  fetched, so a daily refresh takes seconds rather than minutes.
* **Checkpointed** - each batch is written to ``cache/prices/_parts/`` as soon
  as it returns.  A yfinance request can occasionally hang on a single bad
  symbol; because every completed batch is already on disk, a stalled or
  killed run resumes from where it stopped instead of starting over.

All frames are total-return adjusted (``auto_adjust=True``), which is what a
technical model should use: un-adjusted prices create false gaps and false
breakouts on every ex-dividend date.
"""

from __future__ import annotations

import concurrent.futures as _cf
import glob
import json
import os
import shutil
import time

import numpy as np
import pandas as pd
import yfinance as yf

import config as C

FIELDS = ("open", "high", "low", "close", "volume")
_YF_FIELD = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}

MANIFEST = os.path.join(C.PRICE_DIR, "manifest.json")
PARTS_DIR = os.path.join(C.PRICE_DIR, "_parts")
BATCH_TIMEOUT_SEC = 90       # hard ceiling per yfinance batch


# --------------------------------------------------------------------------
# low-level download
# --------------------------------------------------------------------------
def _extract(raw: pd.DataFrame, field: str, tickers) -> pd.DataFrame | None:
    """Pull one field out of a yfinance response into a (date x ticker) frame."""
    if raw is None or len(raw) == 0:
        return None
    yf_field = _YF_FIELD[field]

    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        if yf_field not in set(lvl0):
            return None
        sub = raw[yf_field]
        if isinstance(sub, pd.Series):
            sub = sub.to_frame()
        sub = sub.reindex(columns=list(tickers))
        return sub
    # single-ticker response: flat columns
    if yf_field in raw.columns:
        sub = raw[[yf_field]].copy()
        sub.columns = list(tickers)[:1]
        return sub
    return None


def _download_batch(tickers, period: str, verbose: bool = False,
                    timeout: int = BATCH_TIMEOUT_SEC) -> dict:
    """Download one batch with retries and a hard wall-clock timeout.

    yfinance runs its requests on a thread pool and can hang indefinitely on a
    malformed symbol response.  We therefore run the whole call on a daemon
    worker thread and abandon it if it overruns, rather than blocking the
    entire screen forever.
    """
    last_err = None
    for attempt in range(C.DOWNLOAD_RETRIES):
        pool = _cf.ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(
                yf.download,
                list(tickers),
                period=period,
                interval=C.PRICE_INTERVAL,
                auto_adjust=True,
                progress=False,
                threads=C.DOWNLOAD_THREADS,
                group_by="column",
            )
            raw = fut.result(timeout=timeout)
            out = {}
            for f in FIELDS:
                df = _extract(raw, f, tickers)
                if df is not None:
                    df.index = pd.to_datetime(df.index).tz_localize(None)
                    out[f] = df
            pool.shutdown(wait=False)
            if out and "close" in out and out["close"].notna().any().any():
                return out
            last_err = RuntimeError("empty response")
        except _cf.TimeoutError:
            last_err = TimeoutError(f"batch exceeded {timeout}s")
            pool.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            last_err = e
            pool.shutdown(wait=False)
        time.sleep(1.0 * (attempt + 1))
    if verbose:
        print(f"      batch of {len(tickers)} failed after "
              f"{C.DOWNLOAD_RETRIES} tries: {type(last_err).__name__}: {last_err}")
    return {}


# --------------------------------------------------------------------------
# cache helpers
# --------------------------------------------------------------------------
def _path(field: str) -> str:
    return os.path.join(C.PRICE_DIR, f"{field}.parquet")


def _read_manifest() -> dict:
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST) as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _write_manifest(d: dict) -> None:
    with open(MANIFEST, "w") as fh:
        json.dump(d, fh, indent=2)


def load_cached_prices() -> dict | None:
    """Load the cached wide frames, or None if the cache is incomplete."""
    man = _read_manifest()
    if not man:
        return None
    frames = {}
    for f in FIELDS:
        p = _path(f)
        if not os.path.exists(p):
            return None
        try:
            frames[f] = pd.read_parquet(p)
        except Exception:
            return None
    frames["_meta"] = man
    return frames


# ---- batch checkpoints ---------------------------------------------------
def _part_path(idx: int) -> str:
    return os.path.join(PARTS_DIR, f"batch_{idx:04d}.parquet")


def load_parts() -> tuple[list[pd.DataFrame], set]:
    """Load every checkpointed batch.  Returns (frames, covered tickers)."""
    os.makedirs(PARTS_DIR, exist_ok=True)
    frames, covered = [], set()
    for p in sorted(glob.glob(os.path.join(PARTS_DIR, "batch_*.parquet"))):
        try:
            df = pd.read_parquet(p)
        except Exception:
            continue
        if not isinstance(df.columns, pd.MultiIndex) or df.empty:
            continue
        frames.append(df)
        covered |= set(df.columns.get_level_values(1))
    return frames, covered


def _stack_batch(res: dict, tickers) -> pd.DataFrame:
    """Turn {field: wide frame} into one MultiIndex-column frame for storage."""
    data = {}
    for f in FIELDS:
        df = res.get(f)
        if df is None or not len(df):
            continue
        for t in df.columns:
            data[(f, str(t))] = df[t]
    if not data:
        return pd.DataFrame()
    out = pd.DataFrame(data)
    out.columns = pd.MultiIndex.from_tuples(out.columns, names=["field", "ticker"])
    return out.sort_index(axis=1)


def _split_batches(batch_frames: list[pd.DataFrame]) -> dict:
    """Reconstitute {field: wide frame} from stored batch frames."""
    merged = {f: [] for f in FIELDS}
    for bf in batch_frames:
        for f in FIELDS:
            if f in set(bf.columns.get_level_values(0)):
                sub = bf[f]
                if isinstance(sub, pd.Series):
                    sub = sub.to_frame()
                merged[f].append(sub)
    out = {}
    for f in FIELDS:
        if merged[f]:
            df = pd.concat(merged[f], axis=1)
            out[f] = df.loc[:, ~df.columns.duplicated()]
        else:
            out[f] = pd.DataFrame()
    return out


def _save(frames: dict, tickers, period: str) -> None:
    for f in FIELDS:
        if f in frames:
            frames[f].to_parquet(_path(f))
    _write_manifest(
        {
            "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
            "period": period,
            "n_tickers": int(len(tickers)),
            "tickers": sorted(map(str, tickers)),
        }
    )


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def download_prices(
    tickers,
    period: str = C.PRICE_PERIOD,
    force: bool = False,
    verbose: bool = True,
    progress=None,
) -> dict:
    """Return {open, high, low, close, volume} wide frames for ``tickers``.

    ``progress`` is an optional callable(done, total, message) used by the
    Streamlit dashboard.
    """
    tickers = [t for t in dict.fromkeys(map(str, tickers)) if t]
    # Benchmark + reference ETFs are always kept in the returned matrices (and
    # always refreshed), because the market-regime read needs SPY even though
    # SPY itself is never a screening candidate.
    refs = [C.BENCHMARK] + list(C.BENCHMARK_EXTRA)
    keep = list(dict.fromkeys(tickers + refs))

    if force:
        shutil.rmtree(PARTS_DIR, ignore_errors=True)
        cached = None
    else:
        cached = load_cached_prices()

    have = set()
    merged = {f: None for f in FIELDS}
    if cached:
        for f in FIELDS:
            merged[f] = cached[f]
            have |= set(cached[f].columns)

    # resume from any checkpointed batches (a previous run that was interrupted)
    part_frames, part_covered = ([] if force else load_parts())
    have |= part_covered

    need = [t for t in tickers if t not in have]
    if not force:
        need += [r for r in refs if r not in have]
    else:
        need = list(dict.fromkeys(tickers + refs))
    need = list(dict.fromkeys(need))

    if not need:
        if verbose:
            print(f"  prices: full cache hit ({len(have)} tickers)")
        base = _split_batches(part_frames) if part_frames else {}
        return _finalise(_blend(base, merged), keep)

    batches = [
        need[i : i + C.DOWNLOAD_BATCH_SIZE]
        for i in range(0, len(need), C.DOWNLOAD_BATCH_SIZE)
    ]
    if verbose:
        print(f"  prices: {len(need)} tickers to fetch in {len(batches)} batches "
              f"({len(have)} already cached)")
    if progress:
        progress(0, len(batches), f"0/{len(batches)} batches - "
                                  f"{len(have)} tickers cached")

    os.makedirs(PARTS_DIR, exist_ok=True)
    part_base = len(glob.glob(os.path.join(PARTS_DIR, "batch_*.parquet")))
    fetched, failed = 0, []

    for i, batch in enumerate(batches, 1):
        res = _download_batch(batch, period, verbose=verbose)
        if res:
            stacked = _stack_batch(res, batch)
            try:
                stacked.to_parquet(_part_path(part_base + i))
            except Exception as e:  # noqa: BLE001
                if verbose:
                    print(f"      checkpoint write failed: {e}")
            part_frames.append(stacked)
            c = res.get("close")
            # a ticker only counts as fetched if it actually returned data
            got = set(c.columns[c.notna().any()]) if c is not None else set()
            fetched += len(got)
            failed += [t for t in batch if t not in got]
        else:
            failed += list(batch)

        msg = (f"batch {i}/{len(batches)} - {fetched} tickers fetched, "
               f"{len(failed)} unavailable")
        if progress:
            progress(i, len(batches), msg)
        elif verbose:
            print(f"    {msg}")

    # fold the freshly downloaded parts into the accumulated frames
    fresh = _split_batches(part_frames)
    for f in FIELDS:
        new = fresh.get(f)
        if new is None or not len(new):
            continue
        if merged[f] is not None and len(merged[f]):
            combo = pd.concat([merged[f], new], axis=1)
            merged[f] = combo.loc[:, ~combo.columns.duplicated()]
        else:
            merged[f] = new

    if verbose and failed:
        print(f"    note: {len(failed)} tickers returned no data "
              f"(delisted/renamed) - they are excluded by the history gate")

    _save(merged, have | set(need), period)
    if verbose:
        print()
    return _finalise(merged, keep)


def _blend(*frame_dicts) -> dict:
    """Combine several {field: frame} dicts, first non-null column wins."""
    out = {}
    for f in FIELDS:
        parts = [d[f] for d in frame_dicts if d and d.get(f) is not None
                 and len(d[f])]
        if not parts:
            out[f] = pd.DataFrame()
            continue
        combo = pd.concat(parts, axis=1)
        out[f] = combo.loc[:, ~combo.columns.duplicated()]
    return out


def _finalise(merged: dict, tickers) -> dict:
    """Align all frames on a common index/columns and sort."""
    tickers = [t for t in dict.fromkeys(map(str, tickers))]
    idx = None
    for f in FIELDS:
        if merged.get(f) is not None and len(merged[f]):
            idx = merged[f].index if idx is None else idx.union(merged[f].index)
    if idx is None:
        raise RuntimeError("no price data available at all")
    idx = pd.DatetimeIndex(idx).sort_values()

    out = {}
    for f in FIELDS:
        df = merged.get(f)
        if df is None or not len(df):
            out[f] = pd.DataFrame(index=idx, columns=tickers, dtype="float64")
            continue
        df = df[~df.index.duplicated(keep="last")]
        df = df.reindex(index=idx)
        df = df.reindex(columns=tickers)
        out[f] = df.astype("float64")
    return out


def price_cache_age_days() -> float | None:
    man = _read_manifest()
    ts = man.get("updated_at")
    if not ts:
        return None
    try:
        return (pd.Timestamp.now() - pd.Timestamp(ts)).total_seconds() / 86400.0
    except Exception:
        return None


def coverage_report(prices: dict) -> pd.DataFrame:
    """Per-ticker data quality summary."""
    close = prices["close"]
    bars = close.notna().sum()
    last = close.apply(lambda s: s.last_valid_index())
    return pd.DataFrame(
        {
            "ticker": close.columns,
            "bars": bars.values,
            "last_date": last.values,
            "last_close": close.ffill().iloc[-1].values,
        }
    )


if __name__ == "__main__":
    from universe import load_universe

    u = load_universe()
    t0 = time.time()
    px = download_prices(u["ticker"].tolist(), force="--force" in os.sys.argv)
    print(f"downloaded in {time.time()-t0:.1f}s")
    rep = coverage_report(px)
    print(rep.describe().to_string())
    ok = (rep["bars"] >= C.MIN_BARS_REQUIRED).sum()
    print(f"tickers with >= {C.MIN_BARS_REQUIRED} bars: {ok} / {len(rep)}")
