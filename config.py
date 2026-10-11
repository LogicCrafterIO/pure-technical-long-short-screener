"""
Central configuration for the Pure Technical Long/Short Screener.

Everything tunable lives here so the screener can be re-parameterised without
touching the engine. All values are deliberately conservative, "institutional
default" settings rather than curve-fit optima.
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(ROOT, "cache")
OUTPUT_DIR = os.path.join(ROOT, "output")
UNIVERSE_DIR = os.path.join(CACHE_DIR, "universe")
PRICE_DIR = os.path.join(CACHE_DIR, "prices")

for _d in (CACHE_DIR, OUTPUT_DIR, UNIVERSE_DIR, PRICE_DIR):
    os.makedirs(_d, exist_ok=True)

# --------------------------------------------------------------------------
# Universes
# --------------------------------------------------------------------------
# label -> human readable name
UNIVERSES = {
    "SP500": "S&P 500 (Large Cap)",
    "SP400": "S&P 400 (Mid Cap)",
    "SP600": "S&P 600 (Small Cap)",
    "RUSSELL2000": "Russell 2000 (Small Cap)",
}

# iShares/BlackRock fund used as the authoritative holdings source per universe.
# portfolioId is the BlackRock product id.
ISHARES_FUNDS = {
    "SP500": {"portfolio_id": "239726", "fund": "IVV"},
    "SP400": {"portfolio_id": "239763", "fund": "IJH"},
    "SP600": {"portfolio_id": "239774", "fund": "IJR"},
    "RUSSELL2000": {"portfolio_id": "239710", "fund": "IWM"},
}

WIKI_SP = {
    "SP500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "SP400": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "SP600": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
}

# Universe cache freshness (days). Constituents change rarely; prices change daily.
UNIVERSE_CACHE_DAYS = 7

# --------------------------------------------------------------------------
# Price data
# --------------------------------------------------------------------------
PRICE_PERIOD = "2y"          # yfinance period string
PRICE_INTERVAL = "1d"
DOWNLOAD_BATCH_SIZE = 90     # tickers per yfinance request
DOWNLOAD_RETRIES = 3
DOWNLOAD_THREADS = True
BENCHMARK = "SPY"            # market regime benchmark
BENCHMARK_EXTRA = ["IWM", "QQQ", "TLT"]  # extra reference series for the dashboard

MIN_BARS_REQUIRED = 220      # need >=200 SMA plus buffer

# --------------------------------------------------------------------------
# Hard eligibility gates (liquidity / tradability / shortability)
# --------------------------------------------------------------------------
MIN_PRICE = 5.0              # avoid sub-$5 names (untradable, unshortable)
MAX_PRICE = 100000.0
MIN_DOLLAR_VOLUME = 2_000_000.0   # 20-day average $ volume
EXCLUDE_SECTORS = ()         # e.g. ("Financials",) if you dislike shorting banks

# --------------------------------------------------------------------------
# Indicator parameters
# --------------------------------------------------------------------------
SMA_FAST = 20
SMA_MID = 50
SMA_SLOW = 200
EMA_FAST = 10
EMA_MID = 21
RSI_PERIOD = 14
RSI_SHORT_PERIOD = 2
ATR_PERIOD = 14
ADX_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
BBW_LOOKBACK = 126
STOCH_PERIOD = 14
MFI_PERIOD = 14
CMF_PERIOD = 21
OBV_SLOPE_WINDOW = 63
DONCHIAN_PERIOD = 20
VOL_LOOKBACK_SHORT = 5
VOL_LOOKBACK_LONG = 60
VOLATILITY_WINDOWS = (21, 63)
TRADING_DAYS = 252

# --------------------------------------------------------------------------
# Factor weights  (must sum to 1.0 within each side)
# --------------------------------------------------------------------------
# Each entry: factor_group -> weight. Factor groups are defined in factors.py.
LONG_WEIGHTS = {
    "trend": 0.26,          # multi-timeframe trend structure + ADX quality
    "momentum": 0.24,       # cross-sectional relative momentum (12-1, 6m, 3m)
    "risk_adj_mom": 0.16,   # momentum per unit of volatility
    "rel_strength": 0.10,   # excess return vs the universe
    "volume": 0.12,         # volume/flow confirmation
    "entry": 0.12,          # pullback-in-uptrend entry quality
}

SHORT_WEIGHTS = {
    "trend": 0.27,          # confirmed downtrend structure
    "momentum": 0.23,       # negative relative momentum
    "risk_adj_mom": 0.14,   # downside efficiency
    "rel_strength": 0.10,   # relative weakness vs universe
    "volume": 0.12,         # distribution / selling pressure
    "entry": 0.08,          # rally-into-resistance entry quality
    "squeeze_penalty": 0.06,  # explicit short-squeeze risk deduction
}

# Penalty applied to longs that are over-extended above their short-term mean.
# Over-extension is measured in ATR units; the penalty ramps in beyond
# OVEREXTENSION_START_ATR and is capped at OVEREXTENSION_MAX_PENALTY.
OVEREXTENSION_START_ATR = 2.5
OVEREXTENSION_FULL_ATR = 6.0
OVEREXTENSION_MAX_PENALTY = 1.25     # in composite-score units

# Shallow-pullback bonus: longs sitting modestly below their 20-day mean while
# still in a confirmed uptrend are the highest-expectancy entries.
PULLBACK_BONUS = 0.35

# --------------------------------------------------------------------------
# Cross-sectional normalisation
# --------------------------------------------------------------------------
WINSOR_Z = 3.0               # clip z-scores at +/- this value

# --------------------------------------------------------------------------
# Market regime overlay
# --------------------------------------------------------------------------
REGIME_SMA = 200             # SPY vs its 200d SMA
REGIME_LONG_TILT = 0.80      # multiply long scores when SPY < 200SMA
REGIME_SHORT_TILT = 1.20     # multiply short scores when SPY < 200SMA
BREADTH_LOOKBACK = 50        # % of universe above own 50d SMA

# --------------------------------------------------------------------------
# Trade construction (ATR-based, risk-normalised)
# --------------------------------------------------------------------------
STOP_ATR_MULT = 2.0
TARGET_ATR_MULT = 4.0        # = 2R with the default stop
TRAIL_ATR_MULT = 3.0
ACCOUNT_RISK_PCT = 0.01      # 1% of equity risked per position
ACCOUNT_EQUITY = 100_000.0   # notional reference for share sizing

# --------------------------------------------------------------------------
# Dashboard defaults
# --------------------------------------------------------------------------
DEFAULT_TOP_N = 50
DEFAULT_MIN_PRICE = 5.0
DEFAULT_MAX_PRICE = 500.0

# Column ordering used by the dashboard table — the literal left-to-right
# order, so `Rank L | Rank S | Ticker` is what the reader sees first and the
# view number (`rank`) and composite `score` sit at the right-hand end.
# `stop`, `target` and `score` are resolved per-side by the app before render.
# The CSV explorer applies the same rule to its own selection with
# `prompt_ui.ranks_first`.
DISPLAY_COLUMNS = [
    "rank_long", "rank_short", "ticker", "name", "universe", "sector", "side",
    "close", "chg_1d_pct", "rsi14", "adx14",
    "ret_3m", "ret_6m", "ret_12_1", "rs_ret_3m",
    "atr_pct", "dist_sma50_pct", "dist_sma200_pct",
    "avg_dollar_vol_m", "rvol", "pct_to_52w_high", "stop", "target",
    "risk_pct", "shares_for_1pct_risk", "flags",
    "rank", "score",
]
