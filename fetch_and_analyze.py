#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  TRADE INTEGRITY PIPELINE — Real Market Data Fetcher + Brooks PA Analyzer
═══════════════════════════════════════════════════════════════════════════════

This script:
  1. Reads your trade CSV (TradesFromPDFs.csv)
  2. Fetches REAL 5-minute OHLC candle data from Zerodha or Dhan
  3. Computes actual Brooks Price Action metrics using the real chart
  4. Outputs enriched_trades.json for the dashboard

SETUP:
  pip install -r requirements.txt

  Then create a .env file (or set environment variables):

  For ZERODHA:
    BROKER=zerodha
    KITE_API_KEY=your_api_key
    KITE_ACCESS_TOKEN=your_access_token

  For DHAN (simpler — only access token needed):
    BROKER=dhan
    DHAN_ACCESS_TOKEN=your_access_token

HOW TO GET TOKENS:
  Zerodha: https://kite.trade → Create app → Login → Get access_token via OAuth
  Dhan:    https://api.dhan.co → Dashboard → Generate access token (just 1 token, no client ID)
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import csv
import json
import math
from datetime import datetime, timedelta, date
from pathlib import Path

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CSV_PATH = SCRIPT_DIR / "TradesFromPDFs.csv"
OUTPUT_PATH = SCRIPT_DIR / "dashboard" / "public" / "enriched_trades.json"
CACHE_DIR = SCRIPT_DIR / "candle_cache"
HISTORY_DIR = SCRIPT_DIR / "analysis_history"

# Read from env or .env file
def load_env():
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

load_env()

BROKER = os.environ.get("BROKER", "zerodha").lower()
# Zerodha
KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "")
# Dhan — only access token needed (https://api.dhan.co)
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")

EMA_PERIOD = 20  # 20-period EMA on 5-min candles (standard Brooks)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1: PARSE THE TRADES CSV
# ─────────────────────────────────────────────────────────────────────────────
def parse_trades_csv(path):
    """Parse the pre-paired TradesFromPDFs.csv"""
    trades = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                "trade_num": int(row["Trade #"]),
                "symbol": row["Symbol"].strip(),
                "side": row["Side"].strip(),
                "entry_time": row["Entry Time"].strip(),
                "exit_time": row["Exit Time"].strip(),
                "qty": int(row["Qty"]),
                "entry_price": float(row["Entry Price"]),
                "exit_price": float(row["Exit Price"]),
                "pnl_points": float(row["P&L Points"]),
                "pnl_rupees": float(row["P&L (₹)"]),
                "duration": row["Duration"].strip(),
            })
    return trades


def get_required_fetches(trades):
    """
    Determine which instrument+date combos we need to fetch.
    Returns list of (symbol, date_str) tuples.
    """
    seen = set()
    fetches = []
    for t in trades:
        entry_date = t["entry_time"].split(" ")[0]
        key = (t["symbol"], entry_date)
        if key not in seen:
            seen.add(key)
            fetches.append(key)
    return fetches


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: FETCH 5-MINUTE OHLC CANDLES
# ─────────────────────────────────────────────────────────────────────────────

class ZerodhaFetcher:
    """Fetch candles from Zerodha Kite Connect API."""

    def __init__(self):
        from kiteconnect import KiteConnect
        self.kite = KiteConnect(api_key=KITE_API_KEY)
        self.kite.set_access_token(KITE_ACCESS_TOKEN)
        self._instruments = None

    def _get_instruments(self):
        if self._instruments is None:
            print("  Fetching Zerodha instrument list (NFO)...")
            self._instruments = self.kite.instruments("NFO")
        return self._instruments

    def _find_instrument_token(self, symbol, trade_date):
        """
        Find the instrument token for a symbol like 'BANKNIFTY MAR FUT'.
        Zerodha instrument names look like: BANKNIFTY2632750FUT or similar.
        We match by tradingsymbol containing the base + expiry month + FUT.
        """
        instruments = self._get_instruments()
        parts = symbol.split()
        base = parts[0]            # BANKNIFTY or NIFTY
        month_str = parts[1]       # FEB or MAR
        inst_type = parts[2]       # FUT

        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        target_month = month_map.get(month_str, 0)
        trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        target_year = trade_dt.year

        # Find matching futures instrument
        candidates = []
        for inst in instruments:
            if (inst.get("name") == base and
                inst.get("instrument_type") == "FUT" and
                inst.get("segment") == "NFO-FUT"):
                expiry = inst.get("expiry")
                if expiry and expiry.month == target_month and expiry.year == target_year:
                    candidates.append(inst)

        if not candidates:
            # Fallback: try matching tradingsymbol
            for inst in instruments:
                ts = inst.get("tradingsymbol", "")
                if (base in ts and "FUT" in ts and
                    inst.get("segment") == "NFO-FUT"):
                    expiry = inst.get("expiry")
                    if expiry and expiry.month == target_month and expiry.year == target_year:
                        candidates.append(inst)

        if candidates:
            # Pick the one closest to expiry (monthly contract)
            candidates.sort(key=lambda x: x.get("expiry", date.max))
            return candidates[-1]["instrument_token"]  # latest expiry = monthly

        raise ValueError(f"Could not find instrument token for {symbol} on {trade_date}")

    def fetch_candles(self, symbol, trade_date):
        """Fetch 5-minute candles for the full trading day (9:15 - 15:30 IST)."""
        token = self._find_instrument_token(symbol, trade_date)
        from_dt = datetime.strptime(f"{trade_date} 09:15:00", "%Y-%m-%d %H:%M:%S")
        to_dt = datetime.strptime(f"{trade_date} 15:30:00", "%Y-%m-%d %H:%M:%S")

        data = self.kite.historical_data(token, from_dt, to_dt, "5minute")

        candles = []
        for d in data:
            candles.append({
                "timestamp": d["date"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(d["date"], datetime) else str(d["date"]),
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": int(d.get("volume", 0)),
            })
        return candles


class DhanFetcher:
    """
    Fetch candles from Dhan REST API.
    Only requires access-token (no client ID needed).
    API docs: https://api.dhan.co
    """

    # Two endpoints per https://dhanhq.co/docs/v2/historical-data/
    # POST /v2/charts/historical → daily OHLC
    # POST /v2/charts/intraday   → minute OHLC (1, 5, 15, 25, 60 min)
    INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
    DAILY_URL = "https://api.dhan.co/v2/charts/historical"
    SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

    def __init__(self):
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            "access-token": DHAN_ACCESS_TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._security_map = None

    def _get_security_id(self, symbol, trade_date):
        """
        Map symbol like 'BANKNIFTY MAR FUT' to Dhan security ID.
        Downloads the scrip master CSV from Dhan (cached after first call).

        Scrip master columns:
          SEM_TRADING_SYMBOL: e.g. "BANKNIFTY-Mar2026-FUT"
          SEM_EXCH_INSTRUMENT_TYPE: "FUT" for futures
          SEM_INSTRUMENT_NAME: "FUTIDX" for index futures
          SEM_SMST_SECURITY_ID: the numeric ID we need
          SEM_EXPIRY_DATE: e.g. "2026-03-30 14:30:00"

        NOTE: Expired contracts (e.g. FEB) won't be in the current scrip master.
              For those, we fall back to the nearest available contract (MAR/APR),
              which will have the same underlying's chart data for historical queries.
        """
        parts = symbol.split()
        base = parts[0]            # BANKNIFTY or NIFTY
        month_str = parts[1]       # FEB, MAR, etc.

        month_map = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        month_names = {v: k.capitalize() for k, v in {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
            "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
        }.items()}
        target_month = month_map.get(month_str, 0)
        trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")

        # Download scrip master (one-time download, ~10MB)
        if self._security_map is None:
            print("  Downloading Dhan scrip master (first time only)...")
            self._security_map = pd.read_csv(self.SCRIP_MASTER_URL, low_memory=False)
            print(f"    → Loaded {len(self._security_map)} instruments")

        df = self._security_map

        # Filter: index futures for the right underlying
        # SEM_EXCH_INSTRUMENT_TYPE = "FUT", SEM_INSTRUMENT_NAME = "FUTIDX"
        # Symbol format in scrip master: "BANKNIFTY-Mar2026-FUT", "NIFTY-Mar2026-FUT"
        # Use exact prefix match with "-" to avoid NIFTY matching NIFTYNXT50
        mask = (
            df["SEM_TRADING_SYMBOL"].str.startswith(base + "-", na=False) &
            (df["SEM_EXCH_INSTRUMENT_TYPE"] == "FUT") &
            (df["SEM_INSTRUMENT_NAME"] == "FUTIDX")
        )
        candidates = df[mask].copy()

        if len(candidates) == 0:
            raise ValueError(
                f"No FUTIDX instruments found for {base} in Dhan scrip master. "
                f"Check {self.SCRIP_MASTER_URL}"
            )

        # Parse expiry dates
        candidates["expiry_dt"] = pd.to_datetime(
            candidates["SEM_EXPIRY_DATE"], errors="coerce"
        )

        # Try exact month match first
        exact = candidates[
            (candidates["expiry_dt"].dt.month == target_month) &
            (candidates["expiry_dt"].dt.year == trade_dt.year)
        ]
        if len(exact) > 0:
            exact = exact.sort_values("expiry_dt")
            sec_id = int(exact.iloc[-1]["SEM_SMST_SECURITY_ID"])
            sym_name = exact.iloc[-1]["SEM_TRADING_SYMBOL"]
            print(f"    → Resolved {symbol} → security_id={sec_id} ({sym_name})")
            return sec_id

        # Expired contract not in scrip master — use nearest available
        # (The underlying index chart is the same regardless of contract month)
        candidates = candidates.sort_values("expiry_dt")
        nearest = candidates.iloc[0]  # nearest expiry = front month
        sec_id = int(nearest["SEM_SMST_SECURITY_ID"])
        sym_name = nearest["SEM_TRADING_SYMBOL"]
        print(f"    → {symbol} expired, using nearest contract: security_id={sec_id} ({sym_name})")
        return sec_id

    def fetch_candles(self, symbol, trade_date):
        """
        Fetch 5-minute OHLC candles for the full trading day (9:15-15:30 IST).

        Uses Dhan v2 intraday charts API (per https://dhanhq.co/docs/v2/historical-data/):
          POST https://api.dhan.co/v2/charts/intraday
          Header: access-token: <your_token>
          Body: { securityId, exchangeSegment, instrument, interval, fromDate, toDate }

        Dhan keeps intraday data for 5 years, so expired contracts are available.
        """
        security_id = self._get_security_id(symbol, trade_date)

        # Request body per Dhan docs:
        #   securityId, exchangeSegment, instrument, interval, fromDate, toDate, expiryCode
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": "NSE_FNO",
            "instrument": "FUTIDX",
            "interval": "5",            # 5-minute candles
            "fromDate": trade_date,      # "YYYY-MM-DD"
            "toDate": trade_date,        # same day for intraday
            "expiryCode": 0,
        }

        print(f"    → POST {self.INTRADAY_URL} securityId={security_id}")
        resp = self.session.post(self.INTRADAY_URL, json=payload)

        if resp.status_code != 200:
            raise ValueError(
                f"Dhan API error {resp.status_code} for {symbol} on {trade_date}: "
                f"{resp.text[:500]}"
            )

        data = resp.json()

        # Dhan response format (per docs):
        # { open: [...], high: [...], low: [...], close: [...],
        #   volume: [...], timestamp: [...] }   (epoch seconds)
        candles = []
        timestamps = data.get("timestamp", data.get("start_Time", []))
        opens = data.get("open", [])
        highs = data.get("high", [])
        lows = data.get("low", [])
        closes = data.get("close", [])
        volumes = data.get("volume", [])

        if not timestamps:
            raise ValueError(
                f"Dhan returned empty data for {symbol} on {trade_date}. "
                f"Response: {str(data)[:300]}"
            )

        for i in range(len(timestamps)):
            ts = datetime.fromtimestamp(timestamps[i])
            candles.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": int(volumes[i]) if i < len(volumes) else 0,
            })

        return candles


class YFinanceFetcher:
    """
    FREE data source — fetches NIFTY/BANKNIFTY index 5-min candles from Yahoo Finance.
    No API key, no subscription needed. Works for data up to ~60 days back.

    The index chart structure is identical to futures for Brooks PA analysis
    (same candles, same patterns — just a small futures premium offset).
    """

    # Yahoo Finance tickers for Indian indices
    TICKER_MAP = {
        "BANKNIFTY": "^NSEBANK",
        "NIFTY": "^NSEI",
    }

    def fetch_candles(self, symbol, trade_date):
        """Fetch 5-minute candles for the full trading day via Yahoo Finance."""
        import yfinance as yf

        # Extract base name: "BANKNIFTY FEB FUT" → "BANKNIFTY"
        base = symbol.split()[0]
        yf_ticker = self.TICKER_MAP.get(base)
        if not yf_ticker:
            raise ValueError(f"No Yahoo Finance ticker mapping for {base}")

        # yfinance needs start (inclusive) and end (exclusive, next day)
        from_date = trade_date
        to_dt = datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=1)
        to_date = to_dt.strftime("%Y-%m-%d")

        ticker = yf.Ticker(yf_ticker)
        df = ticker.history(start=from_date, end=to_date, interval="5m")

        if len(df) == 0:
            raise ValueError(
                f"Yahoo Finance returned no data for {yf_ticker} on {trade_date}. "
                f"Data may only be available for the last ~60 days."
            )

        candles = []
        for ts, row in df.iterrows():
            # ts is timezone-aware (IST), convert to naive string
            candles.append({
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row.get("Volume", 0)),
            })

        return candles


def get_fetcher():
    if BROKER == "dhan":
        print("Using DHAN API")
        return DhanFetcher()
    elif BROKER == "zerodha":
        print("Using ZERODHA Kite Connect API")
        return ZerodhaFetcher()
    else:
        print("Using YAHOO FINANCE (free, no API key needed)")
        return YFinanceFetcher()


def fetch_all_candles(trades):
    """
    Fetch candle data for all required symbol/date combos.
    Caches to disk so you don't re-fetch on subsequent runs.
    """
    CACHE_DIR.mkdir(exist_ok=True)
    fetches = get_required_fetches(trades)
    all_candles = {}  # key: "symbol|date" -> list of candle dicts

    # Check cache first
    to_fetch = []
    for symbol, trade_date in fetches:
        cache_key = f"{symbol.replace(' ', '_')}_{trade_date}"
        cache_file = CACHE_DIR / f"{cache_key}.json"
        key = f"{symbol}|{trade_date}"
        if cache_file.exists():
            with open(cache_file, "r") as f:
                all_candles[key] = json.load(f)
            print(f"  ✓ Cached: {symbol} on {trade_date} ({len(all_candles[key])} candles)")
        else:
            to_fetch.append((symbol, trade_date))

    if to_fetch:
        fetcher = get_fetcher()
        for symbol, trade_date in to_fetch:
            key = f"{symbol}|{trade_date}"
            cache_key = f"{symbol.replace(' ', '_')}_{trade_date}"
            cache_file = CACHE_DIR / f"{cache_key}.json"
            try:
                print(f"  ⟳ Fetching: {symbol} on {trade_date}...")
                candles = fetcher.fetch_candles(symbol, trade_date)
                all_candles[key] = candles
                # Cache to disk
                with open(cache_file, "w") as f:
                    json.dump(candles, f)
                print(f"    → Got {len(candles)} candles, cached.")
            except Exception as e:
                print(f"    ✗ FAILED: {e}")
                all_candles[key] = []

    return all_candles


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3: REAL BROOKS PRICE ACTION ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def compute_ema(closes, period=EMA_PERIOD):
    """Compute EMA on a list of close prices."""
    if len(closes) < period:
        return [np.mean(closes[:i+1]) for i in range(len(closes))]
    ema = [np.mean(closes[:period])]
    multiplier = 2 / (period + 1)
    for i in range(period, len(closes)):
        ema.append(closes[i] * multiplier + ema[-1] * (1 - multiplier))
    # Pad front with SMA values
    front = [np.mean(closes[:i+1]) for i in range(period)]
    return front + ema[1:]  # ema[0] = SMA, already in front


def candles_to_df(candles):
    """Convert candle list to pandas DataFrame."""
    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def find_candle_at_time(df, time_str):
    """Find the candle that contains the given timestamp."""
    t = pd.Timestamp(time_str)
    # Find candle where timestamp <= t (the 5-min candle that was active)
    mask = df["timestamp"] <= t
    if mask.any():
        return df[mask].iloc[-1]
    if len(df) > 0:
        return df.iloc[0]
    return None


def find_candles_between(df, start_str, end_str):
    """Get all candles between two timestamps."""
    start = pd.Timestamp(start_str)
    end = pd.Timestamp(end_str)
    mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
    return df[mask]


def is_doji(candle, threshold_pct=0.1):
    """A candle is a doji if body is < threshold% of range."""
    body = abs(candle["close"] - candle["open"])
    rng = candle["high"] - candle["low"]
    if rng == 0:
        return True
    return (body / rng) < threshold_pct


def is_strong_trend_bar(candle):
    """Strong trend bar: body > 60% of total range."""
    body = abs(candle["close"] - candle["open"])
    rng = candle["high"] - candle["low"]
    if rng == 0:
        return False
    return (body / rng) > 0.6


def is_shaved_bar(candle):
    """
    Shaved bar: no wick on one extreme.
    Bull shaved bottom: open == low (or very close)
    Bear shaved bottom: close == low
    Shaved top: close == high (bull) or open == high (bear)
    """
    rng = candle["high"] - candle["low"]
    if rng == 0:
        return False
    tolerance = rng * 0.05  # 5% tolerance
    bull = candle["close"] > candle["open"]
    if bull:
        shaved_bottom = (candle["open"] - candle["low"]) < tolerance
        shaved_top = (candle["high"] - candle["close"]) < tolerance
    else:
        shaved_bottom = (candle["close"] - candle["low"]) < tolerance
        shaved_top = (candle["high"] - candle["open"]) < tolerance
    return shaved_bottom or shaved_top


def detect_barb_wire(df, center_idx, lookback=3):
    """
    Barb Wire: 3+ overlapping bars around center_idx, at least one doji.
    Check if bars overlap (each bar's range intersects with the previous).
    """
    start = max(0, center_idx - lookback)
    end = min(len(df), center_idx + lookback + 1)
    window = df.iloc[start:end]

    if len(window) < 3:
        return False

    # Check overlapping ranges
    overlap_count = 0
    has_doji = False
    for i in range(1, len(window)):
        prev = window.iloc[i - 1]
        curr = window.iloc[i]
        # Bars overlap if high of one >= low of other and vice versa
        overlap = min(prev["high"], curr["high"]) >= max(prev["low"], curr["low"])
        if overlap:
            overlap_count += 1
        if is_doji(curr):
            has_doji = True
        if is_doji(prev):
            has_doji = True

    return overlap_count >= 2 and has_doji


def compute_2hm_real(df, entry_time_str, ema_values):
    """
    Real 2HM: How many minutes was price away from the 20-EMA
    before the entry candle?
    "Away" = all candle closes on the same side of EMA for consecutive bars.
    """
    entry_t = pd.Timestamp(entry_time_str)
    before = df[df["timestamp"] <= entry_t]
    if len(before) == 0 or len(ema_values) == 0:
        return 0

    # Walk backwards from entry, counting consecutive bars on same side
    entry_idx = len(before) - 1
    if entry_idx >= len(ema_values):
        entry_idx = len(ema_values) - 1

    if entry_idx < 0:
        return 0

    entry_side = before.iloc[entry_idx]["close"] > ema_values[entry_idx]
    consecutive = 0

    for i in range(entry_idx, -1, -1):
        if i >= len(ema_values):
            continue
        bar_above = before.iloc[i]["close"] > ema_values[i]
        if bar_above == entry_side:
            consecutive += 1
        else:
            break

    return consecutive * 5  # Each bar = 5 minutes


def detect_gap_bar(df, ema_values, entry_idx):
    """
    Gap Bar: A bar that opens with a gap from the EMA after a long trend.
    The bar's low is above the EMA (bull gap) or high is below EMA (bear gap).
    This is the first EMA gap bar = high-probability fade setup.
    """
    if entry_idx < 1 or entry_idx >= len(ema_values):
        return False

    candle = df.iloc[entry_idx]
    ema = ema_values[entry_idx]

    # Bull gap bar: entire bar above EMA
    bull_gap = candle["low"] > ema
    # Bear gap bar: entire bar below EMA
    bear_gap = candle["high"] < ema

    if not (bull_gap or bear_gap):
        return False

    # Check if previous bars were NOT gap bars (this is the first one)
    for i in range(max(0, entry_idx - 3), entry_idx):
        if i >= len(ema_values):
            continue
        prev = df.iloc[i]
        prev_ema = ema_values[i]
        if bull_gap and prev["low"] > prev_ema:
            return False  # Not the first gap bar
        if bear_gap and prev["high"] < prev_ema:
            return False
    return True


def find_swing_points(df):
    """
    Identify swing highs and lows for leg counting.
    A swing high: bar[i].high > bar[i-1].high and bar[i].high > bar[i+1].high
    A swing low: bar[i].low < bar[i-1].low and bar[i].low < bar[i+1].low
    """
    swings = []  # (index, "high"/"low", price)
    for i in range(1, len(df) - 1):
        h = df.iloc[i]["high"]
        l = df.iloc[i]["low"]
        if h > df.iloc[i-1]["high"] and h > df.iloc[i+1]["high"]:
            swings.append((i, "high", h))
        if l < df.iloc[i-1]["low"] and l < df.iloc[i+1]["low"]:
            swings.append((i, "low", l))
    return swings


def count_legs(swings, entry_idx, is_long):
    """
    Count legs (pushes) in the trend direction up to entry_idx.
    For longs: count successive higher highs. For shorts: count successive lower lows.
    Only reset when a swing in the TREND direction exceeds the prior trend extreme
    (i.e., a new high in a bull trend or new low in a bear trend restarts counting).
    Complex pullbacks (lower highs within a correction) do NOT reset.
    """
    relevant = [s for s in swings if s[0] <= entry_idx]
    if not relevant:
        return 1

    if is_long:
        highs = [s for s in relevant if s[1] == "high"]
        if not highs:
            return 1
        leg = 1
        trend_extreme = highs[0][2]
        for i in range(1, len(highs)):
            if highs[i][2] > trend_extreme:
                leg += 1
                trend_extreme = highs[i][2]
            # Lower highs within a correction are ignored — no reset
        return min(leg, 5)
    else:
        lows = [s for s in relevant if s[1] == "low"]
        if not lows:
            return 1
        leg = 1
        trend_extreme = lows[0][2]
        for i in range(1, len(lows)):
            if lows[i][2] < trend_extreme:
                leg += 1
                trend_extreme = lows[i][2]
            # Higher lows within a correction are ignored — no reset
        return min(leg, 5)


def compute_real_mfe_mae(df, entry_time, exit_time, entry_price, is_long):
    """
    True MFE/MAE from actual candle highs and lows during the trade.
    """
    trade_candles = find_candles_between(df, entry_time, exit_time)
    if len(trade_candles) == 0:
        return 0, 0

    if is_long:
        max_price = trade_candles["high"].max()
        min_price = trade_candles["low"].min()
        mfe = max(0, max_price - entry_price)
        mae = max(0, entry_price - min_price)
    else:
        max_price = trade_candles["high"].max()
        min_price = trade_candles["low"].min()
        mfe = max(0, entry_price - min_price)
        mae = max(0, max_price - entry_price)

    return mfe, mae


def is_stop_run_window(entry_time_str):
    """11:00-11:30 IST is the common stop-run window in Indian markets."""
    t = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
    return t.hour == 11 and t.minute <= 30


def is_middle_of_middle(entry_time_str, df):
    """
    Middle of the Middle: midday (12:00-13:30) AND price in middle 40% of day's range.
    """
    t = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
    if not (12 <= t.hour <= 13):
        return False
    if t.hour == 13 and t.minute > 30:
        return False

    if len(df) == 0:
        return True

    day_high = df["high"].max()
    day_low = df["low"].min()
    day_range = day_high - day_low
    if day_range == 0:
        return True

    entry_candle = find_candle_at_time(df, entry_time_str)
    if entry_candle is None:
        return True

    price = entry_candle["close"]
    pct_in_range = (price - day_low) / day_range
    return 0.3 <= pct_in_range <= 0.7  # Middle 40% of the range


def is_institutional_trap(entry_price, is_long, df, ema_values, entry_idx):
    """
    Institutional trap: entry is where weak-hand stops would cluster.
    For longs: entering near the low of recent range (stop-hunt zone).
    For shorts: entering near the high.
    Also: entry against the EMA often traps the wrong side.
    """
    if len(df) < 10 or entry_idx < 5:
        return False

    # Look at last 20 bars
    lookback = min(20, entry_idx)
    window = df.iloc[entry_idx - lookback:entry_idx + 1]
    hi = window["high"].max()
    lo = window["low"].min()
    rng = hi - lo
    if rng == 0:
        return False

    if is_long:
        # Long near the bottom of recent range = where bears put stops
        position = (entry_price - lo) / rng
        return position < 0.25
    else:
        # Short near the top of recent range = where bulls put stops
        position = (hi - entry_price) / rng
        return position < 0.25


def detect_failed_failure(trade, all_trades, idx):
    """
    Failed Failure: Previous same-symbol trade lost and was opposite direction,
    current trade wins. Indicates a trapped crowd fueling your trade.
    """
    if idx == 0:
        return False
    prev = all_trades[idx - 1]
    if prev["symbol"] != trade["symbol"]:
        return False
    prev_day = prev["entry_time"].split(" ")[0]
    cur_day = trade["entry_time"].split(" ")[0]
    if prev_day != cur_day:
        return False
    if prev["side"] == trade["side"]:
        return False
    return prev["pnl_points"] < 0 and trade["pnl_points"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# NEW: EMA SLOPE, MARKET PHASE, TRENDLINE BREAK DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def compute_ema_slope(ema_values, entry_idx, window=10):
    """
    Compute EMA slope over a lookback window ending at entry_idx.
    Returns (slope_float, classification) where classification is
    "FLAT", "MILD", or "STEEP" based on normalized slope magnitude.
    """
    if not ema_values or entry_idx < 1:
        return 0.0, "FLAT"

    start = max(0, entry_idx - window)
    end = min(entry_idx + 1, len(ema_values))
    segment = ema_values[start:end]

    if len(segment) < 2:
        return 0.0, "FLAT"

    slope = (segment[-1] - segment[0]) / len(segment)

    # Normalize by the EMA level to get a percentage-based threshold
    ema_level = segment[-1] if segment[-1] != 0 else 1.0
    norm_slope = abs(slope / ema_level) * 100  # basis points per bar

    if norm_slope < 0.015:
        classification = "FLAT"
    elif norm_slope < 0.06:
        classification = "MILD"
    else:
        classification = "STEEP"

    return float(slope), classification


def detect_market_phase(df, ema_values, entry_idx):
    """
    Detect the current market phase near entry_idx.
    Returns "SPIKE", "CHANNEL", or "TRADING_RANGE".

    SPIKE: 3+ consecutive strong trend bars (body>60%, small tails) near entry.
    CHANNEL: After a spike, slower drift; EMA slope is MILD.
    TRADING_RANGE: EMA is FLAT, bars overlapping within a defined boundary.
    """
    if len(df) < 3:
        return "TRADING_RANGE"

    if entry_idx < 2:
        window = df.iloc[0:min(6, len(df))]
    else:
        lookback = min(10, entry_idx)
        start = entry_idx - lookback
        window = df.iloc[start:entry_idx + 1]

    consecutive_strong = 0
    direction = None
    for i in range(len(window) - 1, -1, -1):
        bar = window.iloc[i]
        body = abs(bar["close"] - bar["open"])
        rng = bar["high"] - bar["low"]
        if rng == 0:
            break
        body_ratio = body / rng
        if body_ratio > 0.6:
            bar_dir = "up" if bar["close"] > bar["open"] else "down"
            if direction is None:
                direction = bar_dir
            if bar_dir == direction:
                consecutive_strong += 1
            else:
                break
        else:
            break

    if consecutive_strong >= 3:
        return "SPIKE"

    # Single climactic bar: range > 2× average of prior 10 bars = functional Spike
    if len(window) >= 3:
        recent_ranges = [(float(window.iloc[j]["high"]) - float(window.iloc[j]["low"])) for j in range(len(window) - 1)]
        if recent_ranges:
            avg_rng = sum(recent_ranges) / len(recent_ranges)
            last_bar = window.iloc[-1]
            last_rng = float(last_bar["high"]) - float(last_bar["low"])
            if avg_rng > 0 and last_rng > avg_rng * 2.0:
                body = abs(float(last_bar["close"]) - float(last_bar["open"]))
                if last_rng > 0 and body / last_rng > 0.6:
                    return "SPIKE"

    if entry_idx < 2:
        strong_in_window = sum(
            1 for j in range(len(window))
            if (window.iloc[j]["high"] - window.iloc[j]["low"]) > 0
            and abs(window.iloc[j]["close"] - window.iloc[j]["open"]) / (window.iloc[j]["high"] - window.iloc[j]["low"]) > 0.5
        )
        if strong_in_window >= 2:
            return "SPIKE"

    _, slope_class = compute_ema_slope(ema_values, min(entry_idx, len(ema_values) - 1), window=max(1, min(10, entry_idx)))

    if slope_class == "FLAT":
        # Verify overlapping bars (Trading Range hallmark)
        overlap_count = 0
        for i in range(1, len(window)):
            prev = window.iloc[i - 1]
            curr = window.iloc[i]
            if min(prev["high"], curr["high"]) >= max(prev["low"], curr["low"]):
                overlap_count += 1
        overlap_ratio = overlap_count / max(1, len(window) - 1)
        if overlap_ratio >= 0.6:
            return "TRADING_RANGE"
        return "CHANNEL"

    if slope_class == "MILD":
        return "CHANNEL"

    # STEEP slope but no spike streak → could be channel with strong bars
    return "CHANNEL"


def detect_trendline_break(df, swings, entry_idx, is_long):
    """
    Check whether a prior trendline has been broken before entry_idx.
    For a long reversal: connect two swing lows → check if price closed below the line.
    For a short reversal: connect two swing highs → check if price closed above the line.

    Returns True if the trendline was broken (reversal is validated),
    False if the trendline is still intact (reversal should be capped).
    """
    if entry_idx < 3 or len(swings) < 2:
        return False

    relevant = [s for s in swings if s[0] < entry_idx]

    if is_long:
        # For a long reversal, the prior trend was bearish.
        # Bear trendline = connect swing highs. Break = close above it.
        swing_highs = [s for s in relevant if s[1] == "high"]
        if len(swing_highs) < 2:
            return False
        p1 = swing_highs[-2]  # (idx, "high", price)
        p2 = swing_highs[-1]

        idx_span = p2[0] - p1[0]
        if idx_span == 0:
            return False
        slope = (p2[2] - p1[2]) / idx_span

        # Check if any bar between p2 and entry closed above the trendline
        for i in range(p2[0] + 1, min(entry_idx + 1, len(df))):
            tl_price = p2[2] + slope * (i - p2[0])
            if df.iloc[i]["close"] > tl_price:
                return True
        return False
    else:
        # For a short reversal, the prior trend was bullish.
        # Bull trendline = connect swing lows. Break = close below it.
        swing_lows = [s for s in relevant if s[1] == "low"]
        if len(swing_lows) < 2:
            return False
        p1 = swing_lows[-2]
        p2 = swing_lows[-1]

        idx_span = p2[0] - p1[0]
        if idx_span == 0:
            return False
        slope = (p2[2] - p1[2]) / idx_span

        for i in range(p2[0] + 1, min(entry_idx + 1, len(df))):
            tl_price = p2[2] + slope * (i - p2[0])
            if df.iloc[i]["close"] < tl_price:
                return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# INDICATOR BUILDER — 12 Brooks Price Action Detection Functions
# ─────────────────────────────────────────────────────────────────────────────

def detect_failed_final_flag(df, swings, entry_idx, is_long):
    """
    Failed Final Flag: horizontal pause (ii/small TTR) late in trend that
    breaks out and reverses within 1-5 bars, OR 1-2 bars after a climactic
    large trend bar.
    """
    if entry_idx < 5 or len(df) < 6:
        return False

    # Check for climactic bar 1-2 bars before entry
    for lookback in range(1, 3):
        prev_idx = entry_idx - lookback
        if prev_idx < 0:
            continue
        prev = df.iloc[prev_idx]
        body = abs(prev["close"] - prev["open"])
        rng = prev["high"] - prev["low"]
        if rng == 0:
            continue
        if body / rng > 0.7:
            recent_ranges = [
                df.iloc[j]["high"] - df.iloc[j]["low"]
                for j in range(max(0, prev_idx - 10), prev_idx)
            ]
            if recent_ranges:
                avg_range = sum(recent_ranges) / len(recent_ranges)
                if avg_range > 0 and rng > avg_range * 1.5:
                    return True

    # Check for ii pattern / small TTR (horizontal pause) before entry
    pause_count = 0
    for i in range(max(1, entry_idx - 5), entry_idx):
        bar = df.iloc[i]
        prev = df.iloc[i - 1]
        if bar["high"] <= prev["high"] and bar["low"] >= prev["low"]:
            pause_count += 1
        recent_ranges = [
            df.iloc[j]["high"] - df.iloc[j]["low"]
            for j in range(max(0, i - 5), i)
        ]
        if recent_ranges:
            avg_rng = sum(recent_ranges) / len(recent_ranges)
            bar_rng = bar["high"] - bar["low"]
            if avg_rng > 0 and bar_rng < avg_rng * 0.5:
                pause_count += 1

    if pause_count < 2:
        return False

    legs = count_legs(swings, entry_idx, is_long)
    return legs >= 2


def detect_functional_legs(df, swings, entry_idx, is_long):
    """
    Functional Legs: a large tail on a pullback bar acts as a second leg on a
    smaller timeframe. Apparent H1 whose tail dips significantly = Functional H2.
    Returns (is_functional, type_str).
    """
    if entry_idx < 2 or len(df) < 3:
        return False, None

    entry_bar = df.iloc[entry_idx]
    rng = entry_bar["high"] - entry_bar["low"]
    if rng == 0:
        return False, None

    if is_long:
        lower_tail = min(entry_bar["open"], entry_bar["close"]) - entry_bar["low"]
        if lower_tail / rng > 0.4:
            legs = count_legs(swings, entry_idx, is_long)
            if legs <= 1:
                return True, "H1→H2"
    else:
        upper_tail = entry_bar["high"] - max(entry_bar["open"], entry_bar["close"])
        if upper_tail / rng > 0.4:
            legs = count_legs(swings, entry_idx, is_long)
            if legs <= 1:
                return True, "L1→L2"

    return False, None


def detect_shrinking_stairs(df, swings, entry_idx):
    """
    Shrinking Stairs: 3+ trending swings with decreasing breakout distances.
    Signals waning momentum and imminent reversal.
    Returns (is_shrinking, distances_list).
    """
    if len(swings) < 3:
        return False, []

    relevant = [s for s in swings if s[0] <= entry_idx]
    if len(relevant) < 3:
        return False, []

    # Check swing highs (bull stairs)
    highs = [s for s in relevant if s[1] == "high"]
    if len(highs) >= 3:
        distances = [highs[i][2] - highs[i - 1][2] for i in range(1, len(highs))]
        recent = distances[-min(3, len(distances)):]
        if len(recent) >= 2 and all(d > 0 for d in recent):
            if all(recent[i] < recent[i - 1] for i in range(1, len(recent))):
                return True, [round(d, 2) for d in recent]

    # Check swing lows (bear stairs)
    lows = [s for s in relevant if s[1] == "low"]
    if len(lows) >= 3:
        distances = [lows[i - 1][2] - lows[i][2] for i in range(1, len(lows))]
        recent = distances[-min(3, len(distances)):]
        if len(recent) >= 2 and all(d > 0 for d in recent):
            if all(recent[i] < recent[i - 1] for i in range(1, len(recent))):
                return True, [round(d, 2) for d in recent]

    return False, []


def compute_pressure_score(df, entry_idx, lookback=20):
    """
    Pressure Score: quantifies trend pressure via consecutive trend bars,
    shaved bar frequency, and close-at-extreme ratio.
    Returns (score 0-10, consecutive_trend_bars, shaved_bar_frequency).
    """
    start = max(0, entry_idx - lookback)
    window = df.iloc[start:entry_idx + 1]
    if len(window) == 0:
        return 0, 0, 0.0

    first_close = window.iloc[0]["close"]
    last_close = window.iloc[-1]["close"]
    bull_bias = last_close > first_close

    consecutive = 0
    for i in range(len(window) - 1, -1, -1):
        bar = window.iloc[i]
        is_bull = bar["close"] > bar["open"]
        if (bull_bias and is_bull) or (not bull_bias and not is_bull):
            consecutive += 1
        else:
            break

    shaved_count = sum(1 for _, bar in window.iterrows() if is_shaved_bar(bar))
    shaved_freq = shaved_count / len(window)

    extreme_count = 0
    for _, bar in window.iterrows():
        rng = bar["high"] - bar["low"]
        if rng == 0:
            continue
        if bull_bias:
            if (bar["high"] - bar["close"]) / rng < 0.1:
                extreme_count += 1
        else:
            if (bar["close"] - bar["low"]) / rng < 0.1:
                extreme_count += 1
    extreme_ratio = extreme_count / len(window)

    score = (min(consecutive, 5) / 5) * 3 + shaved_freq * 2.5 + extreme_ratio * 2.5

    # 4th component: consecutive EMA gap bars (bars entirely on one side of EMA)
    gap_bar_count = 0
    if entry_idx < len(df):
        for i in range(entry_idx, max(-1, entry_idx - lookback - 1), -1):
            if i < 0 or i >= len(df):
                break
            bar = df.iloc[i]
            bar_low = float(bar["low"])
            bar_high = float(bar["high"])
            mid = (first_close + last_close) / 2
            if bull_bias and bar_low > mid:
                gap_bar_count += 1
            elif not bull_bias and bar_high < mid:
                gap_bar_count += 1
            else:
                break
    score += (min(gap_bar_count, 5) / 5) * 2

    score = max(0, min(10, round(score)))

    return score, consecutive, round(shaved_freq, 3)


def detect_vacuum_magnet(df, entry_idx, entry_price, pdh, pdl, gap, mm):
    """
    Vacuum Effect: price is drawn toward nearby magnets (PDH, PDL, Gap, MM).
    Returns (nearest_magnet_name, distance_in_points).
    """
    magnets = {}
    if pdh is not None and pdh > 0:
        magnets["PDH"] = pdh
    if pdl is not None and pdl > 0:
        magnets["PDL"] = pdl
    if gap is not None:
        magnets["GAP"] = gap
    if mm is not None and mm > 0:
        magnets["MM"] = mm

    if not magnets:
        return None, 0.0

    nearest = None
    min_dist = float("inf")
    for name, level in magnets.items():
        dist = abs(entry_price - level)
        if dist < min_dist:
            min_dist = dist
            nearest = name

    return nearest, round(min_dist, 2)


def detect_breakout_test(df, swings, entry_idx, entry_price):
    """
    Breakout Test: pullback to the exact price level of a prior breakout.
    Returns (is_test, level_or_none).
    """
    if entry_idx < 3 or len(swings) < 2:
        return False, None

    relevant = [s for s in swings if s[0] < entry_idx]
    if len(relevant) < 2:
        return False, None

    tolerance_pct = 0.002
    for swing in reversed(relevant):
        level = swing[2]
        tolerance = level * tolerance_pct
        if abs(entry_price - level) <= tolerance:
            swing_idx = swing[0]
            for j in range(swing_idx + 1, entry_idx):
                if j >= len(df):
                    break
                bar = df.iloc[j]
                if swing[1] == "high" and bar["close"] > level + tolerance:
                    return True, round(level, 2)
                if swing[1] == "low" and bar["close"] < level - tolerance:
                    return True, round(level, 2)

    return False, None


def detect_gap_bar_with_2hm(df, ema_values, entry_idx, two_hm_minutes):
    """
    Gap Bar + 2HM: first EMA gap bar after price has been away from EMA
    for 2+ hours (120 min). High-probability fade setup.
    """
    is_gap = detect_gap_bar(df, ema_values, entry_idx)
    return bool(is_gap and two_hm_minutes >= 120)


def classify_shaved_bar_context(df, swings, entry_idx, is_shaved, is_long):
    """
    Shaved Bar Context: urgency vs exhaustion depends on which leg.
    Leg 1 = institutional urgency (good). Leg 3+ = exhaustion climax (trap).
    Returns (leg_number, is_exhaustion).
    """
    if not is_shaved:
        return 0, False

    legs = count_legs(swings, entry_idx, is_long)
    return legs, legs >= 3


def detect_always_in_status(df, ema_values, swings, entry_idx):
    """
    Always-In Status: at any point, either bulls or bears control the market.
    Uses structural events (massive breakouts, swing breaks) over lagging bar counts.
    Returns "LONG", "SHORT", or "NEUTRAL".
    """
    if entry_idx < 2 or len(df) < 3 or len(ema_values) <= entry_idx:
        return "NEUTRAL"

    close = float(df.iloc[entry_idx]["close"])
    ema = ema_values[entry_idx]

    # Priority 1: Single massive breakout bar flips AIS immediately
    lookback_spike = min(3, entry_idx)
    for i in range(entry_idx, max(-1, entry_idx - lookback_spike - 1), -1):
        if i < 1 or i >= len(df):
            continue
        bar = df.iloc[i]
        body = abs(float(bar["close"]) - float(bar["open"]))
        rng = float(bar["high"]) - float(bar["low"])
        if rng == 0:
            continue
        prev_ranges = [float(df.iloc[j]["high"]) - float(df.iloc[j]["low"])
                       for j in range(max(0, i - 10), i) if j < len(df)]
        avg_rng = sum(prev_ranges) / len(prev_ranges) if prev_ranges else 0
        if avg_rng > 0 and rng > avg_rng * 2.0 and body / rng > 0.6:
            if float(bar["close"]) > float(bar["open"]):
                return "LONG"
            else:
                return "SHORT"

    # Priority 2: Close vs swing structure (structural breakout)
    relevant_highs = [s for s in swings if s[0] <= entry_idx and s[1] == "high"]
    relevant_lows = [s for s in swings if s[0] <= entry_idx and s[1] == "low"]

    if relevant_highs and close > relevant_highs[-1][2]:
        return "LONG"
    if relevant_lows and close < relevant_lows[-1][2]:
        return "SHORT"

    # Priority 3: Bar count + EMA position (fallback)
    lookback = min(10, entry_idx)
    bull_bars = 0
    bear_bars = 0
    for i in range(entry_idx - lookback, entry_idx + 1):
        if i < 0 or i >= len(df):
            continue
        bar = df.iloc[i]
        if float(bar["close"]) > float(bar["open"]):
            bull_bars += 1
        elif float(bar["close"]) < float(bar["open"]):
            bear_bars += 1

    total = bull_bars + bear_bars
    if total == 0:
        return "NEUTRAL"

    bull_pct = bull_bars / total
    if close > ema and bull_pct >= 0.6:
        return "LONG"
    elif close < ema and bull_pct <= 0.4:
        return "SHORT"

    return "NEUTRAL"


def compute_prior_day_levels(all_candles, symbol, trade_date):
    """
    Prior trading day's high, low, and opening gap.
    Searches up to 5 calendar days back to find the prior session.
    Returns (prior_day_high, prior_day_low, opening_gap).
    """
    trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")

    for days_back in range(1, 6):
        prev_date = (trade_dt - timedelta(days=days_back)).strftime("%Y-%m-%d")
        prev_key = f"{symbol}|{prev_date}"
        if prev_key in all_candles and all_candles[prev_key]:
            prev_candles = all_candles[prev_key]
            prev_high = max(c["high"] for c in prev_candles)
            prev_low = min(c["low"] for c in prev_candles)

            today_key = f"{symbol}|{trade_date}"
            opening_gap = None
            if today_key in all_candles and all_candles[today_key]:
                today_open = all_candles[today_key][0]["open"]
                prev_close = prev_candles[-1]["close"]
                opening_gap = round(today_open - prev_close, 2)

            return round(prev_high, 2), round(prev_low, 2), opening_gap

    return 0.0, 0.0, None


def detect_spike_origin(df, ema_values, entry_idx, market_phase):
    """
    Spike Origin: in CHANNEL phase, identify the spike start price that
    initiated the current channel. Acts as a price magnet.
    Returns the spike origin price or None.
    """
    if market_phase != "CHANNEL" or entry_idx < 5:
        return None

    spike_end = None
    for i in range(entry_idx, -1, -1):
        if i >= len(df):
            continue
        bar = df.iloc[i]
        body = abs(bar["close"] - bar["open"])
        rng = bar["high"] - bar["low"]
        if rng > 0 and body / rng > 0.6:
            if spike_end is None:
                spike_end = i
        else:
            if spike_end is not None and spike_end - i >= 3:
                spike_start_bar = df.iloc[i + 1]
                return round(float(spike_start_bar["open"]), 2)
            spike_end = None

    if spike_end is not None and spike_end >= 2:
        return round(float(df.iloc[0]["open"]), 2)

    return None


def detect_barb_wire_zones(df):
    """
    Session-level continuous barb wire zone detection.
    Scans for contiguous stretches of 3+ overlapping/doji bars.
    Returns list of {startIdx, endIdx, startTime, endTime}.
    """
    if len(df) < 3:
        return []

    bw_flags = [False] * len(df)
    for i in range(1, len(df) - 1):
        if detect_barb_wire(df, i, lookback=2):
            bw_flags[i] = True

    zones = []
    zone_start = None
    for i, flag in enumerate(bw_flags):
        if flag and zone_start is None:
            zone_start = i
        elif not flag and zone_start is not None:
            if i - zone_start >= 3:
                ts_start = df.iloc[zone_start]["timestamp"]
                ts_end = df.iloc[i - 1]["timestamp"]
                zones.append({
                    "startIdx": int(zone_start),
                    "endIdx": int(i - 1),
                    "startTime": ts_start.strftime("%H:%M") if hasattr(ts_start, "strftime") else str(ts_start),
                    "endTime": ts_end.strftime("%H:%M") if hasattr(ts_end, "strftime") else str(ts_end),
                })
            zone_start = None

    if zone_start is not None and len(df) - zone_start >= 3:
        ts_start = df.iloc[zone_start]["timestamp"]
        ts_end = df.iloc[len(df) - 1]["timestamp"]
        zones.append({
            "startIdx": int(zone_start),
            "endIdx": int(len(df) - 1),
            "startTime": ts_start.strftime("%H:%M") if hasattr(ts_start, "strftime") else str(ts_start),
            "endTime": ts_end.strftime("%H:%M") if hasattr(ts_end, "strftime") else str(ts_end),
        })

    return zones


# ─────────────────────────────────────────────────────────────────────────────
# INSTITUTIONAL STRUCTURE — Advanced Brooks Price Action Indicators
# ─────────────────────────────────────────────────────────────────────────────

def detect_trend_from_open(df, ema_values):
    """
    Trend From Open (TFO): the opening price IS one extreme of the day
    and price trends relentlessly toward the opposite extreme.  You could
    enter on bar 1, 2, or 3 and it just goes.

    The key test is the SESSION OPEN proximity — "Trend FROM the Open"
    means the open price itself is at or near the day's extreme:
      Bear TFO: open is near the session high → trends down all day
      Bull TFO: open is near the session low  → trends up all day

    Returns (is_tfo, tfo_direction) where direction is "BULL"/"BEAR"/None.
    """
    if len(df) < 10 or len(ema_values) < 10:
        return False, None

    session_open = float(df.iloc[0]["open"])
    session_high = float(df["high"].max())
    session_low = float(df["low"].min())
    session_range = session_high - session_low
    if session_range == 0:
        return False, None

    open_from_high = (session_high - session_open) / session_range
    open_from_low = (session_open - session_low) / session_range

    has_bear_conviction = False
    has_bull_conviction = False
    for i in range(min(3, len(df))):
        bar = df.iloc[i]
        body = abs(float(bar["close"]) - float(bar["open"]))
        rng = float(bar["high"]) - float(bar["low"])
        if rng > 0 and body / rng > 0.4:
            if float(bar["close"]) < float(bar["open"]):
                has_bear_conviction = True
            else:
                has_bull_conviction = True

    half = min(len(df) // 2, len(ema_values))
    if half <= 3:
        return False, None

    n = half - 3

    if open_from_low < 0.20 and has_bull_conviction:
        above = sum(1 for i in range(3, half) if float(df.iloc[i]["close"]) > ema_values[i])
        if above / n > 0.70:
            return True, "BULL"

    if open_from_high < 0.20 and has_bear_conviction:
        below = sum(1 for i in range(3, half) if float(df.iloc[i]["close"]) < ema_values[i])
        if below / n > 0.70:
            return True, "BEAR"

    return False, None


def detect_m2s_m2b(df, ema_values, swings, entry_idx, is_long):
    """
    M2B (Moving average Buy 2nd entry) / M2S (Short 2nd entry):
    Two-legged pullback where at least one bar touches or penetrates the
    20-bar EMA.  These are the highest-probability re-entries in a trend.

    Returns (is_m2b, is_m2s).
    """
    if entry_idx < 5 or entry_idx >= len(ema_values) or len(swings) < 3:
        return False, False

    relevant_swings = [s for s in swings if s[0] <= entry_idx]

    if is_long:
        recent_lows = [s for s in relevant_swings if s[1] == "low"]
        if len(recent_lows) >= 2:
            l1, l2 = recent_lows[-2], recent_lows[-1]
            touched = False
            for sw in (l1, l2):
                for i in range(max(0, sw[0] - 1), min(len(df), sw[0] + 2)):
                    if i < len(ema_values) and float(df.iloc[i]["low"]) <= ema_values[i] * 1.002:
                        touched = True
                        break
                if touched:
                    break
            if touched and entry_idx < len(ema_values):
                ema_rising = ema_values[entry_idx] > ema_values[max(0, entry_idx - 5)]
                if ema_rising:
                    return True, False

    if not is_long:
        recent_highs = [s for s in relevant_swings if s[1] == "high"]
        if len(recent_highs) >= 2:
            h1, h2 = recent_highs[-2], recent_highs[-1]
            touched = False
            for sw in (h1, h2):
                for i in range(max(0, sw[0] - 1), min(len(df), sw[0] + 2)):
                    if i < len(ema_values) and float(df.iloc[i]["high"]) >= ema_values[i] * 0.998:
                        touched = True
                        break
                if touched:
                    break
            if touched and entry_idx < len(ema_values):
                ema_falling = ema_values[entry_idx] < ema_values[max(0, entry_idx - 5)]
                if ema_falling:
                    return False, True

    return False, False


def detect_test_of_extreme(df, swings, entry_idx, is_long):
    """
    Test of Extreme: trends typically end only after a trendline break
    followed by a test of the prior extreme.  The test may overshoot
    (Higher High / Lower Low) or undershoot (Lower High / Higher Low).

    A reversal taken without a completed test is a "beginner's trap."

    Returns (test_type, is_complete).
    test_type: "OVERSHOOT_HIGH"|"UNDERSHOOT_HIGH"|"OVERSHOOT_LOW"|"UNDERSHOOT_LOW"|None
    """
    if entry_idx < 5 or len(swings) < 4:
        return None, False

    relevant = [s for s in swings if s[0] <= entry_idx]
    highs = [s for s in relevant if s[1] == "high"]
    lows = [s for s in relevant if s[1] == "low"]

    if is_long and len(lows) >= 2:
        major_low = min(lows, key=lambda s: s[2])
        test_low = lows[-1]
        if major_low[0] < test_low[0]:
            intervening_highs = [h for h in highs if major_low[0] < h[0] < test_low[0]]
            if intervening_highs:
                if test_low[2] < major_low[2]:
                    return "OVERSHOOT_LOW", True
                elif test_low[2] > major_low[2]:
                    return "UNDERSHOOT_LOW", True

    if not is_long and len(highs) >= 2:
        major_high = max(highs, key=lambda s: s[2])
        test_high = highs[-1]
        if major_high[0] < test_high[0]:
            intervening_lows = [l for l in lows if major_high[0] < l[0] < test_high[0]]
            if intervening_lows:
                if test_high[2] > major_high[2]:
                    return "OVERSHOOT_HIGH", True
                elif test_high[2] < major_high[2]:
                    return "UNDERSHOOT_HIGH", True

    return None, False


def detect_channel_line_break(df, swings, entry_idx):
    """
    Trend Channel Line (TCL) overshoot detection.
    Bull channel: trendline on swing lows, TCL parallel through swing highs.
    Bear channel: trendline on swing highs, TCL parallel through swing lows.
    An overshoot of the TCL signals a climactic reversal — prime institutional
    trap territory.

    Returns "BULL_OVERSHOOT"|"BEAR_OVERSHOOT"|None
    """
    if entry_idx < 5 or len(swings) < 4:
        return None

    relevant = [s for s in swings if s[0] <= entry_idx]
    highs = [s for s in relevant if s[1] == "high"]
    lows = [s for s in relevant if s[1] == "low"]

    if len(lows) >= 2 and len(highs) >= 1:
        bl1, bl2 = lows[-2], lows[-1]
        span = bl2[0] - bl1[0]
        if span > 0:
            bull_slope = (bl2[2] - bl1[2]) / span
            max_dist = 0
            tcl_anchor = None
            for h in highs:
                if h[0] >= bl1[0]:
                    tl_at_h = bl1[2] + bull_slope * (h[0] - bl1[0])
                    dist = h[2] - tl_at_h
                    if dist > max_dist:
                        max_dist = dist
                        tcl_anchor = h
            if tcl_anchor is not None and max_dist > 0:
                for i in range(max(0, entry_idx - 3), min(entry_idx + 1, len(df))):
                    tcl_at_i = tcl_anchor[2] + bull_slope * (i - tcl_anchor[0])
                    if float(df.iloc[i]["high"]) > tcl_at_i:
                        return "BULL_OVERSHOOT"

    if len(highs) >= 2 and len(lows) >= 1:
        bh1, bh2 = highs[-2], highs[-1]
        span = bh2[0] - bh1[0]
        if span > 0:
            bear_slope = (bh2[2] - bh1[2]) / span
            max_dist = 0
            tcl_anchor = None
            for l in lows:
                if l[0] >= bh1[0]:
                    tl_at_l = bh1[2] + bear_slope * (l[0] - bh1[0])
                    dist = tl_at_l - l[2]
                    if dist > max_dist:
                        max_dist = dist
                        tcl_anchor = l
            if tcl_anchor is not None and max_dist > 0:
                for i in range(max(0, entry_idx - 3), min(entry_idx + 1, len(df))):
                    tcl_at_i = tcl_anchor[2] + bear_slope * (i - tcl_anchor[0])
                    if float(df.iloc[i]["low"]) < tcl_at_i:
                        return "BEAR_OVERSHOOT"

    return None


def detect_pd_level_interaction(df, entry_idx, entry_price, is_long, pdh, pdl):
    """
    Prior Day Level interaction classifier.
    Determines whether the entry is near PDH/PDL and whether the market
    is failing at the level (high-prob reversal) or testing it (continuation).

    Failed BO of PDL on a TR day = high-probability reversal long.
    BO Test of PDL on a trend day = continuation short.

    Returns (interaction_type, proximity_pts).
    interaction_type: "FAILED_BO_PDH"|"FAILED_BO_PDL"|"BO_TEST_PDH"|"BO_TEST_PDL"|None
    """
    if pdh == 0 and pdl == 0:
        return None, 0.0

    tolerance = entry_price * 0.002
    pdh_dist = abs(entry_price - pdh) if pdh > 0 else float("inf")
    pdl_dist = abs(entry_price - pdl) if pdl > 0 else float("inf")

    if pdh_dist > tolerance and pdl_dist > tolerance:
        return None, round(min(pdh_dist, pdl_dist), 2)

    if pdh_dist <= tolerance and pdh > 0:
        broke_above = False
        failed_back = False
        for i in range(max(0, entry_idx - 5), min(entry_idx + 1, len(df))):
            if float(df.iloc[i]["high"]) > pdh:
                broke_above = True
            if broke_above and float(df.iloc[i]["close"]) < pdh:
                failed_back = True
        if broke_above and failed_back and not is_long:
            return "FAILED_BO_PDH", round(pdh_dist, 2)
        if not broke_above and is_long:
            return "BO_TEST_PDH", round(pdh_dist, 2)

    if pdl_dist <= tolerance and pdl > 0:
        broke_below = False
        failed_back = False
        for i in range(max(0, entry_idx - 5), min(entry_idx + 1, len(df))):
            if float(df.iloc[i]["low"]) < pdl:
                broke_below = True
            if broke_below and float(df.iloc[i]["close"]) > pdl:
                failed_back = True
        if broke_below and failed_back and is_long:
            return "FAILED_BO_PDL", round(pdl_dist, 2)
        if not broke_below and not is_long:
            return "BO_TEST_PDL", round(pdl_dist, 2)

    return None, round(min(pdh_dist, pdl_dist), 2)


# ─────────────────────────────────────────────────────────────────────────────
# BROOKS COMPLIANCE v2 — New detection functions
# ─────────────────────────────────────────────────────────────────────────────

def detect_tr_volatility(df, entry_idx):
    """
    Distinguish Quiet TR (low ATR, tight overlaps) from Volatile TR
    (Big Up / Big Down swings within a flat EMA environment).
    Returns "QUIET_TR" | "VOLATILE_TR" | None.
    """
    if entry_idx < 5 or len(df) < 10:
        return None

    lookback = min(20, entry_idx + 1)
    window = df.iloc[max(0, entry_idx - lookback + 1):entry_idx + 1]
    if len(window) < 5:
        return None

    ranges = [(float(row["high"]) - float(row["low"])) for _, row in window.iterrows()]
    atr = sum(ranges) / len(ranges) if ranges else 0
    if atr == 0:
        return "QUIET_TR"

    session_high = float(window["high"].max())
    session_low = float(window["low"].min())
    session_range = session_high - session_low

    ratio = session_range / atr if atr > 0 else 0
    return "VOLATILE_TR" if ratio > 6 else "QUIET_TR"


def classify_trendline_significance(df, swings, entry_idx):
    """
    Classify trendline as MICRO (2 touches, short span) or MAJOR
    (3+ touches, wide span). Major trendline break unlocks full reversal
    permission; micro break only permits a scalp.
    Returns "MICRO" | "MAJOR" | None.
    """
    if len(swings) < 2 or entry_idx < 3:
        return None

    relevant = [s for s in swings if s[0] <= entry_idx]
    highs = [s for s in relevant if s[1] == "high"]
    lows = [s for s in relevant if s[1] == "low"]

    best_significance = None

    for point_set in (lows, highs):
        if len(point_set) < 2:
            continue
        p1, p2 = point_set[-2], point_set[-1]
        span = p2[0] - p1[0]
        if span <= 0:
            continue

        slope = (p2[2] - p1[2]) / span
        tolerance_pct = 0.002

        touch_count = 0
        for s in point_set:
            if s[0] < p1[0] or s[0] > entry_idx:
                continue
            expected = p1[2] + slope * (s[0] - p1[0])
            if abs(s[2] - expected) <= abs(expected) * tolerance_pct:
                touch_count += 1

        if touch_count >= 3 and span >= 10:
            return "MAJOR"
        elif touch_count >= 2 and span >= 3:
            if best_significance is None:
                best_significance = "MICRO"

    return best_significance


def detect_barb_wire_location(df, entry_idx, ema_values, ema_slope_class, market_phase):
    """
    Barb Wire in the middle of a range = MIDDLE (no-trade zone).
    Barb Wire that forms as a tight flag in a trend = FLAG (continuation).
    Returns "MIDDLE" | "FLAG" | None.
    """
    if entry_idx < 3 or len(df) < 5:
        return None

    if market_phase in ("SPIKE", "CHANNEL") and ema_slope_class in ("MILD", "STEEP"):
        return "FLAG"

    if entry_idx < len(ema_values):
        day_high = float(df["high"].max())
        day_low = float(df["low"].min())
        day_range = day_high - day_low
        if day_range > 0:
            price = float(df.iloc[entry_idx]["close"])
            pct = (price - day_low) / day_range
            if 0.3 <= pct <= 0.7:
                return "MIDDLE"

    return "MIDDLE"


def detect_climactic_outlier(df, entry_idx):
    """
    Flag bars whose range exceeds 2× the 20-bar ATR as climactic outliers.
    Returns (is_outlier, outlier_type).
    outlier_type: "SUSTAINABLE_SPIKE" if follow-through, "HUGE_BAR_FAILURE" if not.
    """
    if entry_idx < 3 or len(df) < 5:
        return False, None

    lookback = min(20, entry_idx)
    recent = df.iloc[max(0, entry_idx - lookback):entry_idx]
    if len(recent) == 0:
        return False, None

    ranges = [(float(row["high"]) - float(row["low"])) for _, row in recent.iterrows()]
    atr = sum(ranges) / len(ranges) if ranges else 0
    if atr == 0:
        return False, None

    entry_bar = df.iloc[entry_idx]
    entry_range = float(entry_bar["high"]) - float(entry_bar["low"])

    if entry_range <= atr * 2:
        return False, None

    follow_through = 0
    entry_bull = float(entry_bar["close"]) > float(entry_bar["open"])
    for i in range(entry_idx + 1, min(entry_idx + 4, len(df))):
        bar = df.iloc[i]
        if entry_bull and float(bar["close"]) > float(bar["open"]):
            follow_through += 1
        elif not entry_bull and float(bar["close"]) < float(bar["open"]):
            follow_through += 1

    if follow_through >= 2:
        return True, "SUSTAINABLE_SPIKE"
    return True, "HUGE_BAR_FAILURE"


def detect_five_tick_failure(df, swings, entry_idx, is_long, entry_price):
    """
    Five-Tick Failure: prior opposite-direction setup reached within ~5 ticks
    of a scalper's profit target (1× signal bar range) then reversed.
    This trapped crowd fuels the current trade.
    Returns True if a 5-tick failure preceded the entry.
    """
    if entry_idx < 5 or len(df) < 6:
        return False

    tick_size = entry_price * 0.0005
    if tick_size == 0:
        return False

    for lookback in range(1, min(6, entry_idx)):
        setup_idx = entry_idx - lookback
        setup_bar = df.iloc[setup_idx]
        setup_range = float(setup_bar["high"]) - float(setup_bar["low"])
        if setup_range < tick_size:
            continue

        target_dist = setup_range
        threshold = target_dist - (5 * tick_size)
        if threshold <= 0:
            continue

        if is_long:
            if float(setup_bar["close"]) < float(setup_bar["open"]):
                reached = float(setup_bar["open"]) - float(df.iloc[min(setup_idx + 1, len(df) - 1)]["low"])
                if reached >= threshold and reached < target_dist:
                    for j in range(setup_idx + 1, entry_idx + 1):
                        if j < len(df) and float(df.iloc[j]["close"]) > float(setup_bar["high"]):
                            return True
        else:
            if float(setup_bar["close"]) > float(setup_bar["open"]):
                reached = float(df.iloc[min(setup_idx + 1, len(df) - 1)]["high"]) - float(setup_bar["open"])
                if reached >= threshold and reached < target_dist:
                    for j in range(setup_idx + 1, entry_idx + 1):
                        if j < len(df) and float(df.iloc[j]["close"]) < float(setup_bar["low"]):
                            return True

    return False


def compute_vacuum_velocity(df, entry_idx, vacuum_name, vacuum_dist, entry_price):
    """
    Vacuum Velocity: combines magnet proximity with momentum toward the magnet.
    High velocity near a magnet = exhaustion climax (dangerous for continuation).
    Returns a score 0-10 (10 = fastest approach to nearest magnet).
    """
    if vacuum_name is None or vacuum_dist <= 0 or entry_idx < 3:
        return 0

    tick_size = entry_price * 0.0005 if entry_price > 0 else 1
    dist_ticks = vacuum_dist / tick_size if tick_size > 0 else 0

    proximity_score = max(0, 5 - dist_ticks) * 2
    proximity_score = min(5, max(0, proximity_score))

    lookback = min(5, entry_idx)
    window = df.iloc[max(0, entry_idx - lookback):entry_idx + 1]
    if len(window) < 2:
        return round(proximity_score)

    direction_bars = 0
    for i in range(len(window)):
        bar = window.iloc[i]
        is_bull = float(bar["close"]) > float(bar["open"])
        body_ratio = abs(float(bar["close"]) - float(bar["open"])) / max(float(bar["high"]) - float(bar["low"]), 0.01)
        if body_ratio > 0.5:
            direction_bars += 1

    momentum_score = (direction_bars / len(window)) * 5

    velocity = min(10, round(proximity_score + momentum_score))
    return velocity


def compute_gap_bar_pressure(df, ema_values, entry_idx):
    """
    Count consecutive bars entirely on one side of the EMA leading up to entry.
    In a runaway trend, bars staying entirely above/below EMA = max pressure.
    Returns the count of consecutive EMA gap bars.
    """
    if entry_idx < 1 or not ema_values or entry_idx >= len(ema_values):
        return 0

    consecutive = 0
    for i in range(entry_idx, -1, -1):
        if i >= len(df) or i >= len(ema_values):
            continue
        bar = df.iloc[i]
        ema = ema_values[i]
        if float(bar["low"]) > ema:
            consecutive += 1
        elif float(bar["high"]) < ema:
            consecutive += 1
        else:
            break

    return consecutive


def compute_session_atr(df):
    """Compute average true range for the session (for outlier/fat-finger detection)."""
    if len(df) < 2:
        return 0.0
    ranges = []
    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]
        tr = max(
            float(curr["high"]) - float(curr["low"]),
            abs(float(curr["high"]) - float(prev["close"])),
            abs(float(curr["low"]) - float(prev["close"])),
        )
        ranges.append(tr)
    return sum(ranges) / len(ranges) if ranges else 0.0


def compute_session_trendlines(df, swings):
    """
    Compute bull and bear trendlines for the full session.
    Bull trendline: connect the two most recent swing lows. Broken when close < line.
    Bear trendline: connect the two most recent swing highs. Broken when close > line.
    Returns the most recent trendline of each type.
    """
    if len(df) < 3 or len(swings) < 2:
        return []

    trendlines = []

    swing_lows = [s for s in swings if s[1] == "low"]
    if len(swing_lows) >= 2:
        p1 = swing_lows[-2]
        p2 = swing_lows[-1]
        idx_span = p2[0] - p1[0]
        if idx_span > 0:
            slope = (p2[2] - p1[2]) / idx_span
            broken = False
            broken_at = None
            for i in range(p2[0] + 1, len(df)):
                tl_price = p2[2] + slope * (i - p2[0])
                if df.iloc[i]["close"] < tl_price:
                    broken = True
                    broken_at = i
                    break
            trendlines.append({
                "type": "bull",
                "startIdx": int(p1[0]),
                "endIdx": int(p2[0]),
                "startPrice": round(float(p1[2]), 2),
                "endPrice": round(float(p2[2]), 2),
                "broken": broken,
                "brokenAtIdx": int(broken_at) if broken_at is not None else None,
            })

    swing_highs = [s for s in swings if s[1] == "high"]
    if len(swing_highs) >= 2:
        p1 = swing_highs[-2]
        p2 = swing_highs[-1]
        idx_span = p2[0] - p1[0]
        if idx_span > 0:
            slope = (p2[2] - p1[2]) / idx_span
            broken = False
            broken_at = None
            for i in range(p2[0] + 1, len(df)):
                tl_price = p2[2] + slope * (i - p2[0])
                if df.iloc[i]["close"] > tl_price:
                    broken = True
                    broken_at = i
                    break
            trendlines.append({
                "type": "bear",
                "startIdx": int(p1[0]),
                "endIdx": int(p2[0]),
                "startPrice": round(float(p1[2]), 2),
                "endPrice": round(float(p2[2]), 2),
                "broken": broken,
                "brokenAtIdx": int(broken_at) if broken_at is not None else None,
            })

    return trendlines


def detect_market_phase_regions(df, ema_values):
    """
    Walk through the session in 10-bar windows and classify each region as
    SPIKE, CHANNEL, or TRADING_RANGE. Adjacent windows with the same phase
    are merged into a single region.
    """
    if len(df) < 3:
        return []

    window_size = 10
    regions = []
    i = 0

    while i < len(df):
        end = min(i + window_size, len(df))
        window = df.iloc[i:end]

        if len(window) < 3:
            phase = "TRADING_RANGE"
        else:
            # Count consecutive strong trend bars in this window
            consecutive_strong = 0
            direction = None
            for j in range(len(window) - 1, -1, -1):
                bar = window.iloc[j]
                body = abs(bar["close"] - bar["open"])
                rng = bar["high"] - bar["low"]
                if rng == 0:
                    break
                if body / rng > 0.6:
                    bar_dir = "up" if bar["close"] > bar["open"] else "down"
                    if direction is None:
                        direction = bar_dir
                    if bar_dir == direction:
                        consecutive_strong += 1
                    else:
                        break
                else:
                    break

            if consecutive_strong >= 3:
                phase = "SPIKE"
            else:
                mid_idx = min(end - 1, len(ema_values) - 1)
                if mid_idx >= 1:
                    _, slope_class = compute_ema_slope(ema_values, mid_idx, window=min(10, mid_idx))
                else:
                    slope_class = "FLAT"

                if slope_class == "FLAT":
                    overlap_count = 0
                    for k in range(1, len(window)):
                        prev = window.iloc[k - 1]
                        curr = window.iloc[k]
                        if min(prev["high"], curr["high"]) >= max(prev["low"], curr["low"]):
                            overlap_count += 1
                    overlap_ratio = overlap_count / max(1, len(window) - 1)
                    phase = "TRADING_RANGE" if overlap_ratio >= 0.6 else "CHANNEL"
                elif slope_class == "MILD":
                    phase = "CHANNEL"
                else:
                    phase = "CHANNEL"

        if regions and regions[-1]["phase"] == phase:
            regions[-1]["endIdx"] = end - 1
        else:
            regions.append({
                "phase": phase,
                "startIdx": i,
                "endIdx": end - 1,
            })

        i = end

    return regions


# ─────────────────────────────────────────────────────────────────────────────
# BROOKS SCORECARD — NOW WITH REAL DATA
# ─────────────────────────────────────────────────────────────────────────────

def compute_brooks_score(trade, df, ema_values, swings, entry_idx, all_trades, trade_idx, flags):
    """
    Weighted 1-10 Brooks Score using REAL candle data.

    Total = Context × 25% + Setup × 25% + Signal × 20% + Institutional × 20% + Pressure × 10%

    Returns (score_dict, decomposition_dict).
    """
    is_long = trade["side"] == "LONG"
    market_phase = flags.get("marketPhase", "TRADING_RANGE")
    ema_slope_class = flags.get("emaSlopeClassification", "FLAT")
    clamp = lambda v: max(1, min(10, round(v)))

    # ── CONTEXT (base 5) ──
    context = 5
    ctx_reasons = []

    with_trend = flags.get("withTrend", False)
    two_hm = flags.get("twoHM", 0)
    is_countertrend = not with_trend

    tr_vol = flags.get("trVolatility")

    if ema_slope_class == "STEEP" and with_trend:
        context += 3
        ctx_reasons.append("With-trend + STEEP EMA slope (+3)")
    elif ema_slope_class == "FLAT":
        if entry_idx < len(df):
            day_high = df["high"].max()
            day_low = df["low"].min()
            day_range = day_high - day_low
            if day_range > 0:
                entry_price = trade["entry_price"]
                pct_in_range = (entry_price - day_low) / day_range
                at_extreme = pct_in_range < 0.25 or pct_in_range > 0.75
            else:
                at_extreme = False
        else:
            at_extreme = False

        if not with_trend and at_extreme:
            bonus = 4 if tr_vol == "VOLATILE_TR" else 3
            context += bonus
            ctx_reasons.append(f"Countertrend fade at range extreme in FLAT {tr_vol or 'TR'} (+{bonus})")
        elif with_trend:
            penalty = -4 if tr_vol == "VOLATILE_TR" else -3
            context += penalty
            ctx_reasons.append(f"With-trend in FLAT {tr_vol or 'TR'} mid-range = false signal ({penalty})")
    elif with_trend:
        context += 2
        ctx_reasons.append(f"With-trend in {ema_slope_class} slope (+2)")

    if two_hm >= 120:
        context += 3
        ctx_reasons.append(f"2HM={two_hm}min (>=120) — strong trend (+3)")
    elif flags.get("isStopRun", False):
        context += 2
        ctx_reasons.append("Stop-run window 11:00-11:30 (+2)")

    if flags.get("isMiddleOfMiddle", False):
        context -= 5
        ctx_reasons.append("Middle of Middle penalty (-5)")

    if market_phase == "SPIKE":
        context += 2
        ctx_reasons.append("SPIKE phase (+2)")
    elif market_phase == "TRADING_RANGE" and with_trend:
        context -= 2
        ctx_reasons.append("TRADING_RANGE breakout entry (-2)")

    # TFO: Trend from Open auto-boosts with-trend; penalizes counter-TFO
    tfo_dir = flags.get("tfoDirection")
    if flags.get("isTFO", False) and tfo_dir:
        tfo_aligned = (tfo_dir == "BULL" and is_long) or (tfo_dir == "BEAR" and not is_long)
        if tfo_aligned:
            context += 3
            ctx_reasons.append(f"TFO {tfo_dir} — with-trend auto-boost (+3)")
        else:
            context -= 3
            ctx_reasons.append(f"Counter-TFO ({tfo_dir}) — Swing-Only mode (-3)")

    # Trendline Break gate: micro break = scalp permission (cap 5), major break = full reversal
    trendline_intact = flags.get("trendlineIntact", True)
    tl_sig = flags.get("trendlineSignificance")
    if trendline_intact and is_countertrend:
        context = min(context, 3)
        ctx_reasons.append("No trendline break — reversal CTX capped at 3")
    elif not trendline_intact and is_countertrend and tl_sig == "MICRO":
        context = min(context, 5)
        ctx_reasons.append("MICRO trendline break only — reversal CTX capped at 5 (scalp)")

    context = clamp(context)

    # ── SETUP RELIABILITY (base 5) ──
    setup = 5
    setup_reasons = []
    legs = flags.get("leg", 1)

    if legs == 2:
        setup += 3
        setup_reasons.append("Leg 2 — second entry, highest reliability (+3)")
    elif legs == 1:
        setup += 1
        setup_reasons.append("Leg 1 — first entry (+1)")
    elif legs == 3:
        setup -= 2
        setup_reasons.append("Leg 3 — Wedge territory, overextended (-2)")
    elif legs >= 4:
        setup -= 4
        setup_reasons.append(f"Leg {legs} — trend is dead, original direction exhausted (-4)")

    if flags.get("isFunctionalLeg", False):
        setup += 2
        flt = flags.get("functionalLegType", "")
        setup_reasons.append(f"Functional leg ({flt}) (+2)")

    if flags.get("isFailedFinalFlag", False):
        setup += 2
        setup_reasons.append("Failed Final Flag (+2)")
    if flags.get("isFailedFailure", False):
        setup += 2
        setup_reasons.append("Failed Failure (trapped crowd) (+2)")

    # M2B/M2S: bread-and-butter second entries at Fair Value (EMA)
    if flags.get("isM2B", False):
        setup += 3
        setup_reasons.append("M2B — 2-leg pullback to EMA in bull trend (+3)")
    elif flags.get("isM2S", False):
        setup += 3
        setup_reasons.append("M2S — 2-leg rally to EMA in bear trend (+3)")

    # EMA Gap Bar + 2HM ≥ 120min = Exhaustion Fade trigger
    if flags.get("gapBarWith2HM", False):
        setup += 3
        setup_reasons.append("EMA Gap Bar + 2HM — Exhaustion Fade trigger (+3)")

    if trendline_intact and is_countertrend:
        setup = min(setup, 5)
        setup_reasons.append("Trendline intact + reversal → capped at 5")
    elif not trendline_intact and is_countertrend and tl_sig == "MICRO":
        setup = min(setup, 6)
        setup_reasons.append("MICRO trendline break only — reversal SET capped at 6")

    # Five-tick failure: prior setup almost hit target then reversed — trapped crowd fuel
    if flags.get("isFiveTickFailure", False):
        setup += 2
        setup_reasons.append("Five-tick failure — prior setup trapped at target (+2)")

    if flags.get("isShrinkingStairs", False) and with_trend:
        setup -= 2
        setup_reasons.append("Shrinking stairs + with-trend = waning momentum (-2)")

    # TFO with-trend H2/L2: in a Trend from Open, any H2/L2 is maximum reliability
    if flags.get("isTFO", False) and legs == 2:
        tfo_dir = flags.get("tfoDirection")
        tfo_aligned = (tfo_dir == "BULL" and is_long) or (tfo_dir == "BEAR" and not is_long)
        if tfo_aligned:
            setup = max(setup, 10)
            setup_reasons.append("TFO + Leg 2 — institutional alignment = 10/10 SET")

    setup = clamp(setup)

    # ── SIGNAL BAR (base 5) ──
    signal = 5
    sig_reasons = []
    entry_candle = df.iloc[entry_idx] if entry_idx < len(df) else None

    if entry_candle is not None:
        if is_strong_trend_bar(entry_candle) and market_phase in ("SPIKE", "CHANNEL"):
            signal += 2
            sig_reasons.append(f"Strong trend bar in {market_phase} (+2)")

        shaved_leg = flags.get("shavedBarLeg", 0)
        if flags.get("isShavedBar", False):
            if shaved_leg <= 1:
                signal += 2
                sig_reasons.append("Shaved bar on Leg 1 — urgency (+2)")
            elif shaved_leg >= 3:
                signal -= 2
                sig_reasons.append(f"Shaved bar on Leg {shaved_leg} — exhaustion (-2)")

        if is_doji(entry_candle):
            if market_phase == "SPIKE":
                signal += 1
                sig_reasons.append("Doji on pullback in SPIKE (+1)")
            elif market_phase == "TRADING_RANGE" and flags.get("isBarbWire", False):
                bw_loc = flags.get("barbWireLocation")
                if bw_loc == "FLAG":
                    signal -= 1
                    sig_reasons.append("Doji in barb wire FLAG (trend context) (-1)")
                else:
                    signal -= 3
                    sig_reasons.append("Doji in barb wire MIDDLE — no-trade zone (-3)")
            elif market_phase == "TRADING_RANGE":
                signal -= 2
                sig_reasons.append("Doji in TRADING_RANGE (-2)")

        # Climactic outlier: massive bar could be exhaustion or sustainable spike
        if flags.get("isClimaticOutlier", False):
            outlier_type = flags.get("climaticOutlierType")
            if outlier_type == "HUGE_BAR_FAILURE":
                signal -= 2
                sig_reasons.append("Climactic outlier — no follow-through (HUGE_BAR_FAILURE) (-2)")
            elif outlier_type == "SUSTAINABLE_SPIKE":
                signal += 1
                sig_reasons.append("Climactic outlier with follow-through (SUSTAINABLE_SPIKE) (+1)")

    signal = clamp(signal)

    # ── INSTITUTIONAL (base 5) ──
    inst = 5
    inst_reasons = []

    if flags.get("isInstitutionalTrap", False):
        inst += 2
        inst_reasons.append("Institutional trap — entry in stop-hunt zone (+2)")
    if flags.get("isFailedFailure", False):
        inst += 2
        inst_reasons.append("Failed Failure (Failed L2/H2) — trapped crowd (+2)")
    if flags.get("isBreakoutTest", False):
        inst += 2
        bl = flags.get("breakoutTestLevel")
        inst_reasons.append(f"Breakout test at {bl} (+2)")

    vacuum_name = flags.get("vacuumMagnet")
    vacuum_dist = flags.get("vacuumDistance", 0)
    vacuum_vel = flags.get("vacuumVelocity", 0)
    if vacuum_name is not None:
        tick_size = trade["entry_price"] * 0.0005
        dist_ticks = vacuum_dist / tick_size if tick_size > 0 else 0
        if dist_ticks < 5 and vacuum_vel >= 7:
            inst -= 3
            inst_reasons.append(f"Vacuum {vacuum_name} {dist_ticks:.0f} ticks + velocity {vacuum_vel}/10 — exhaustion climax (-3)")
        elif dist_ticks < 5:
            inst -= 2
            inst_reasons.append(f"Vacuum {vacuum_name} only {dist_ticks:.0f} ticks away — late entry (-2)")
        elif vacuum_dist > 0:
            inst += 1
            inst_reasons.append(f"Vacuum {vacuum_name} at {vacuum_dist:.0f}pts — magnet pull (+1)")

    ais = flags.get("alwaysInStatus", "NEUTRAL")
    if ais != "NEUTRAL":
        ais_matches = (ais == "LONG" and is_long) or (ais == "SHORT" and not is_long)
        if ais_matches:
            inst += 2
            inst_reasons.append(f"Always-In {ais} matches direction (+2)")
        else:
            inst -= 2
            inst_reasons.append(f"Always-In {ais} opposes direction (-2)")

    # Test of Extreme: reversals without a completed test are beginner traps
    test_type = flags.get("testOfExtreme")
    test_complete = flags.get("testOfExtremeComplete", False)
    if is_countertrend:
        if test_complete and test_type:
            is_overshoot = "OVERSHOOT" in test_type
            if is_overshoot:
                inst += 2
                inst_reasons.append(f"Test of extreme: {test_type} — overshoot trap (+2)")
            else:
                inst += 1
                inst_reasons.append(f"Test of extreme: {test_type} — undershoot, weaker reversal (+1)")
        else:
            inst = min(inst, 5)
            inst_reasons.append("No test of extreme — reversal INST capped at 5")

    # Channel Line Break: overshoot = prime institutional climactic trap
    channel_brk = flags.get("channelLineBreak")
    if channel_brk:
        inst += 3
        inst_reasons.append(f"Channel line {channel_brk} — climactic reversal trap (+3)")

    # PDH/PDL interaction: failed breakout vs continuation test
    pd_int = flags.get("pdInteraction")
    if pd_int:
        if "FAILED_BO" in pd_int:
            inst += 2
            inst_reasons.append(f"{pd_int} — failed breakout at major magnet (+2)")
        elif "BO_TEST" in pd_int:
            inst += 1
            inst_reasons.append(f"{pd_int} — breakout test for continuation (+1)")

    inst = clamp(inst)

    # ── PRESSURE (base 5, use pressureScore 0-10 → map to 1-10) ──
    raw_pressure = flags.get("pressureScore", 0)
    gap_bar_prs = flags.get("gapBarPressure", 0)
    if gap_bar_prs >= 3:
        raw_pressure = min(10, raw_pressure + 2)
    pressure = max(1, min(10, raw_pressure)) if raw_pressure > 0 else 1
    prs_reasons = []
    consec = flags.get("consecutiveTrendBars", 0)
    shaved_freq = flags.get("shavedBarFrequency", 0)
    prs_reasons.append(f"Pressure score {raw_pressure}/10 (consec={consec}, shavedFreq={shaved_freq:.2f})")
    if gap_bar_prs >= 3:
        prs_reasons.append(f"EMA gap bar streak={gap_bar_prs} — runaway trend amplifier (+2)")

    # ── SPECIAL: Stop-Run + Failed Failure → auto-floor at 9 ──
    is_special = flags.get("isStopRun", False) and flags.get("isFailedFailure", False)
    if is_special:
        prs_reasons.append("SPECIAL: isStopRun + isFailedFailure → auto-floor 9/10")

    # ── TOTAL: 25/25/20/20/10 ──
    total = round(
        context * 0.25 + setup * 0.25 + signal * 0.20 + inst * 0.20 + pressure * 0.10
    )
    total = clamp(total)

    if is_special:
        total = max(total, 9)

    score_dict = {
        "total": total,
        "context": context,
        "setupReliability": setup,
        "signalBar": signal,
        "institutional": inst,
        "pressure": pressure,
    }

    decomposition = {
        "context": {"score": context, "reasons": ctx_reasons},
        "setupReliability": {"score": setup, "reasons": setup_reasons},
        "signalBar": {"score": signal, "reasons": sig_reasons},
        "institutional": {"score": inst, "reasons": inst_reasons},
        "pressure": {"score": pressure, "reasons": prs_reasons},
    }

    return score_dict, decomposition


def classify_trade_quality(score_total, pnl):
    """GOOD_WIN / GOOD_LOSS / BAD_WIN / BAD_LOSS based on score and P&L."""
    if score_total >= 6:
        return "GOOD_WIN" if pnl > 0 else "GOOD_LOSS"
    else:
        return "BAD_WIN" if pnl > 0 else "BAD_LOSS"


def assign_mistake_tag(flags, trade, all_trades, trade_idx, context_score):
    """Assign ONE primary mistake tag based on flags + outcome."""
    if flags.get("isBarbWire", False) and flags.get("barbWireLocation") != "FLAG":
        return "#BARB_WIRE"
    if context_score <= 3:
        return "#LOW_CONTEXT"
    if flags.get("leg", 1) >= 3:
        return "#OVEREXTENDED"

    # Revenge: 2+ consecutive same-symbol losses within 30 min
    if trade_idx > 0 and trade["pnl_points"] <= 0:
        prev = all_trades[trade_idx - 1]
        if prev["symbol"] == trade["symbol"] and prev["pnl_points"] < 0:
            try:
                prev_exit = datetime.strptime(prev["exit_time"], "%Y-%m-%d %H:%M:%S")
                cur_entry = datetime.strptime(trade["entry_time"], "%Y-%m-%d %H:%M:%S")
                if (cur_entry - prev_exit).total_seconds() <= 1800:
                    return "#REVENGE"
            except (ValueError, KeyError):
                pass

    mp = flags.get("marketPhase", "")
    if mp == "TRADING_RANGE" and flags.get("withTrend", False):
        return "#FOMO"

    score_total = flags.get("_score_total", 0)
    if score_total >= 8:
        return "#PERFECT_STORM"
    if score_total >= 6 and trade["pnl_points"] > 0:
        return "#GOOD_ENTRY"

    return ""


def generate_thesis(flags, trade):
    """Auto-generate a thesis string from flags describing what the setup looked like at entry."""
    parts = []
    side = trade["side"]
    mp = flags.get("marketPhase", "TR")
    parts.append(f"{side} entry in {mp} phase")

    leg = flags.get("leg", 1)
    parts.append(f"Leg {leg}")

    if flags.get("withTrend"):
        slope = flags.get("emaSlopeClassification", "FLAT")
        parts.append(f"with-trend (EMA {slope})")
    else:
        parts.append("countertrend")

    if flags.get("isFailedFinalFlag"):
        parts.append("after failed final flag")
    if flags.get("isFailedFailure"):
        parts.append("failed failure (trapped crowd)")
    if flags.get("isBreakoutTest"):
        parts.append(f"breakout test at {flags.get('breakoutTestLevel')}")
    if flags.get("isBarbWire"):
        parts.append("in barb wire zone")
    if flags.get("isStopRun"):
        parts.append("during stop-run window")
    if flags.get("isFunctionalLeg"):
        parts.append(f"functional leg ({flags.get('functionalLegType', '')})")

    ais = flags.get("alwaysInStatus", "NEUTRAL")
    if ais != "NEUTRAL":
        parts.append(f"AIS={ais}")

    if flags.get("isTFO"):
        parts.append(f"Trend from Open ({flags.get('tfoDirection', '')})")
    if flags.get("isM2B"):
        parts.append("M2B (2-leg pullback to EMA)")
    if flags.get("isM2S"):
        parts.append("M2S (2-leg rally to EMA)")
    test_ext = flags.get("testOfExtreme")
    if test_ext:
        parts.append(f"test of extreme: {test_ext}")
    ch_brk = flags.get("channelLineBreak")
    if ch_brk:
        parts.append(f"channel line break: {ch_brk}")
    pd_int = flags.get("pdInteraction")
    if pd_int:
        parts.append(f"PD level: {pd_int}")

    return ". ".join(parts) + "."


def generate_reality(flags, trade):
    """Auto-generate a reality string describing what actually happened."""
    pnl = trade["pnl_points"]
    parts = []

    if pnl > 0:
        parts.append(f"Won {abs(pnl):.1f} pts")
    else:
        parts.append(f"Lost {abs(pnl):.1f} pts")

    if flags.get("reachedMM"):
        parts.append("reached measured move target")
    else:
        parts.append("did not reach measured move")

    if flags.get("isShrinkingStairs"):
        parts.append("momentum was waning (shrinking stairs)")

    vacuum = flags.get("vacuumMagnet")
    if vacuum:
        parts.append(f"vacuum magnet ({vacuum}) was nearby")

    if flags.get("channelLineBreak"):
        parts.append(f"channel line {flags['channelLineBreak']} occurred — climactic")
    if flags.get("testOfExtremeComplete"):
        parts.append(f"test of extreme was complete ({flags.get('testOfExtreme', '')})")
    pd_int = flags.get("pdInteraction")
    if pd_int:
        parts.append(f"prior day level interaction: {pd_int}")

    return ". ".join(parts) + "."


# ─────────────────────────────────────────────────────────────────────────────
# ANALOGY DEBRIEF — Unchanged (pattern-based)
# ─────────────────────────────────────────────────────────────────────────────

ANALOGIES = {
    "failedFailure": [
        "This was a 'Poker Check-Raise'—you let the other side think they had momentum before the trap door opened.",
        "Classic 'Rope-a-Dope': absorbed the loss, then capitalized as trapped traders fueled the reversal.",
    ],
    "withTrend": [
        "Like catching a bus you can see coming—this with-trend entry had the institutional tailwind behind it.",
        "This was a 'Surfing the Wave' trade: entered on the pullback, rode the momentum like a seasoned waterman.",
    ],
    "barbWire": [
        "This was 'Fighting in Quicksand'—every move was absorbed by the chop. Barb Wire claimed another victim.",
        "Like trying to sprint through molasses: the Barb Wire stalemate devoured edge and patience equally.",
    ],
    "middleMiddle": [
        "This was 'No-Man's Land'—midday, mid-range, the market was telling you to wait, not trade.",
        "Like picking a fight at halftime: nobody was playing, and the referee (the EMA) was off duty.",
    ],
    "secondEntry": [
        "A textbook 'Second Swing at the Piñata'—the first attempt showed the opening, the second one scored.",
        "This M2B/M2S was a 'Confirmation Retest': letting the market prove itself before committing capital.",
    ],
    "stopRun": [
        "The 11:00 AM stop run: like clockwork, weak hands got flushed and you rode the institutional re-entry.",
        "This was the 'Morning Shakeout Sniper'—the market hunted stops, and you were there for the bounce.",
    ],
    "overextended": [
        "A 3rd/4th push trade: like squeezing a lemon that's already been juiced twice. Diminishing returns.",
        "This was 'Chasing the Train'—by the 3rd leg, the smart money was already stepping off.",
    ],
    "shavedBar": [
        "High urgency entry with a shaved bar: the market was screaming its intent with no wick to hide behind.",
        "This 'Full Send' candle left no ambiguity—shaved extremes show institutional commitment.",
    ],
    "gapBar": [
        "This Gap Bar fade was a 'Rubber Band Trade'—stretched too far from the EMA, the snap-back was inevitable.",
        "Classic 'Mean Reversion Sniper': the gap bar screamed exhaustion, and you took the fade.",
    ],
    "tfo": [
        "Trend from the Open: the market declared war in the first bar and never looked back. With-trend was the only play.",
        "This was a TFO day: the open set one extreme and the market marched to the opposite. Every pullback was a gift.",
    ],
    "m2b_m2s": [
        "M2B at the EMA: two legs down to fair value, then the trend resumed. This is the bread and butter.",
        "This M2S/M2B was the institutional re-entry: two chances to get on board at the moving average.",
    ],
    "channelBreak": [
        "Channel line overshoot: the trend got euphoric, broke its own speed limit, and the institutions faded the excess.",
        "This was a 'Rubber Band Snap': the channel line break marked the climax. The reversal was institutional, not retail.",
    ],
    "pdLevel": [
        "A Failed Breakout of yesterday's level: the market lured breakout traders past the prior day extreme and trapped them.",
        "Prior Day Level interaction: this is where the big orders sit. Failure here is a high-probability reversal.",
    ],
    "generic": [
        "Solid execution within the price action framework. The key is consistency: did the market agree with your thesis?",
        "Every trade is a sentence in your market diary. This one reads: 'I respected the context, the setup spoke, and I listened.'",
    ],
}

def pick_analogy(flags):
    import random
    if flags.get("isFailedFailure"): return random.choice(ANALOGIES["failedFailure"])
    if flags.get("channelLineBreak"): return random.choice(ANALOGIES["channelBreak"])
    if flags.get("pdInteraction") and "FAILED_BO" in flags.get("pdInteraction", ""): return random.choice(ANALOGIES["pdLevel"])
    if flags.get("isTFO"): return random.choice(ANALOGIES["tfo"])
    if flags.get("isM2B") or flags.get("isM2S"): return random.choice(ANALOGIES["m2b_m2s"])
    if flags.get("isBarbWire"): return random.choice(ANALOGIES["barbWire"])
    if flags.get("isMiddleOfMiddle"): return random.choice(ANALOGIES["middleMiddle"])
    if flags.get("leg") == 2: return random.choice(ANALOGIES["secondEntry"])
    if flags.get("isStopRun"): return random.choice(ANALOGIES["stopRun"])
    if flags.get("leg", 0) >= 3: return random.choice(ANALOGIES["overextended"])
    if flags.get("isShavedBar"): return random.choice(ANALOGIES["shavedBar"])
    if flags.get("isGapBar"): return random.choice(ANALOGIES["gapBar"])
    if flags.get("pnl", 0) > 0: return random.choice(ANALOGIES["withTrend"])
    return random.choice(ANALOGIES["generic"])


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4: ORCHESTRATE — Build enriched output
# ─────────────────────────────────────────────────────────────────────────────

def analyze_trade(trade, df, ema_values, swings, all_trades, trade_idx, all_candles=None):
    """Analyze a single trade with real candle data."""
    is_long = trade["side"] == "LONG"
    entry_candle = find_candle_at_time(df, trade["entry_time"])
    entry_idx = 0
    if entry_candle is not None and len(df) > 0:
        match = df[df["timestamp"] == entry_candle["timestamp"]]
        if len(match) > 0:
            entry_idx = match.index[0]

    # Real MFE / MAE
    mfe, mae = compute_real_mfe_mae(
        df, trade["entry_time"], trade["exit_time"],
        trade["entry_price"], is_long
    )

    # Real patterns
    two_hm = compute_2hm_real(df, trade["entry_time"], ema_values)
    bw = detect_barb_wire(df, entry_idx)
    gap = detect_gap_bar(df, ema_values, entry_idx)
    shaved = is_shaved_bar(entry_candle) if entry_candle is not None else False
    legs = count_legs(swings, entry_idx, is_long)
    ff = detect_failed_failure(trade, all_trades, trade_idx)
    trap = is_institutional_trap(trade["entry_price"], is_long, df, ema_values, entry_idx)
    stop_run = is_stop_run_window(trade["entry_time"])
    mom = is_middle_of_middle(trade["entry_time"], df)

    # NEW: Market phase, EMA slope, trendline break
    market_phase = detect_market_phase(df, ema_values, entry_idx)
    ema_slope, ema_slope_class = compute_ema_slope(ema_values, entry_idx, window=10)
    trendline_intact = not detect_trendline_break(df, swings, entry_idx, is_long)

    # NEW: 12 indicator-builder detections
    trade_date = trade["entry_time"].split(" ")[0]
    pdh, pdl, opening_gap = compute_prior_day_levels(
        all_candles or {}, trade["symbol"], trade_date
    )

    failed_final_flag = detect_failed_final_flag(df, swings, entry_idx, is_long)
    func_leg, func_leg_type = detect_functional_legs(df, swings, entry_idx, is_long)
    shrinking, stair_dists = detect_shrinking_stairs(df, swings, entry_idx)
    pressure, consec_trend, shaved_freq = compute_pressure_score(df, entry_idx)

    # Measured move target from recent swing structure (for vacuum magnet)
    mm_target_price = None
    r_highs = [s for s in swings if s[0] <= entry_idx and s[1] == "high"]
    r_lows = [s for s in swings if s[0] <= entry_idx and s[1] == "low"]
    if is_long and r_lows and r_highs:
        leg_dist = r_highs[-1][2] - r_lows[-1][2]
        if leg_dist > 0:
            mm_target_price = r_lows[-1][2] + leg_dist * 2
    elif not is_long and r_highs and r_lows:
        leg_dist = r_highs[-1][2] - r_lows[-1][2]
        if leg_dist > 0:
            mm_target_price = r_highs[-1][2] - leg_dist * 2

    # Gap fill level = yesterday's close (the magnet price gets sucked toward)
    gap_level = None
    if opening_gap is not None and abs(opening_gap) > 0:
        today_key = f"{trade['symbol']}|{trade_date}"
        if all_candles and today_key in all_candles and all_candles[today_key]:
            gap_level = all_candles[today_key][0]["open"] - opening_gap

    vacuum_name, vacuum_dist = detect_vacuum_magnet(
        df, entry_idx, trade["entry_price"], pdh, pdl, gap_level, mm_target_price
    )
    bo_test, bo_level = detect_breakout_test(df, swings, entry_idx, trade["entry_price"])
    gap_2hm = detect_gap_bar_with_2hm(df, ema_values, entry_idx, two_hm)
    shaved_leg, shaved_exhaust = classify_shaved_bar_context(
        df, swings, entry_idx, shaved, is_long
    )
    ais = detect_always_in_status(df, ema_values, swings, entry_idx)
    spike_origin = detect_spike_origin(df, ema_values, entry_idx, market_phase)

    # Institutional Structure detections
    is_tfo, tfo_direction = detect_trend_from_open(df, ema_values)
    is_m2b, is_m2s = detect_m2s_m2b(df, ema_values, swings, entry_idx, is_long)
    test_extreme_type, test_extreme_complete = detect_test_of_extreme(df, swings, entry_idx, is_long)
    channel_break = detect_channel_line_break(df, swings, entry_idx)
    pd_interaction, pd_proximity = detect_pd_level_interaction(
        df, entry_idx, trade["entry_price"], is_long, pdh, pdl
    )

    # Brooks Compliance v2 detections
    tr_vol = detect_tr_volatility(df, entry_idx) if market_phase == "TRADING_RANGE" else None
    tl_significance = classify_trendline_significance(df, swings, entry_idx)
    bw_location = detect_barb_wire_location(df, entry_idx, ema_values, ema_slope_class, market_phase) if bw else None
    is_climactic, climactic_type = detect_climactic_outlier(df, entry_idx)
    five_tick = detect_five_tick_failure(df, swings, entry_idx, is_long, trade["entry_price"])
    vacuum_vel = compute_vacuum_velocity(df, entry_idx, vacuum_name, vacuum_dist, trade["entry_price"])
    gap_bar_prs = compute_gap_bar_pressure(df, ema_values, entry_idx)

    # Real signal bar analysis
    signal_bar_info = None
    if entry_candle is not None:
        signal_bar_info = {
            "open": float(entry_candle["open"]),
            "high": float(entry_candle["high"]),
            "low": float(entry_candle["low"]),
            "close": float(entry_candle["close"]),
            "isDoji": is_doji(entry_candle),
            "isStrongTrend": is_strong_trend_bar(entry_candle),
            "isShaved": is_shaved_bar(entry_candle),
        }

    # Measured move: first pullback's range * 2
    pullback = abs(trade["exit_price"] - trade["entry_price"])
    mm_target = pullback * 2
    reached_mm = mfe >= mm_target and mm_target > 0

    # With-trend check using real EMA
    with_trend = False
    if entry_idx < len(ema_values) and entry_candle is not None:
        ema_at_entry = ema_values[entry_idx]
        if is_long and entry_candle["close"] > ema_at_entry:
            with_trend = True
        elif not is_long and entry_candle["close"] < ema_at_entry:
            with_trend = True

    flags = {
        "isBarbWire": bool(bw),
        "twoHM": int(two_hm),
        "isShavedBar": bool(shaved),
        "isGapBar": bool(gap),
        "leg": int(legs),
        "isFailedFailure": bool(ff),
        "isInstitutionalTrap": bool(trap),
        "isStopRun": bool(stop_run),
        "isMiddleOfMiddle": bool(mom),
        "withTrend": bool(with_trend),
        "reachedMM": bool(reached_mm),
        "measuredMoveTarget": float(mm_target),
        "pnl": float(trade["pnl_rupees"]),
        "marketPhase": market_phase,
        "emaSlope": float(ema_slope),
        "emaSlopeClassification": ema_slope_class,
        "trendlineIntact": bool(trendline_intact),
        "isFailedFinalFlag": bool(failed_final_flag),
        "isFunctionalLeg": bool(func_leg),
        "functionalLegType": func_leg_type,
        "isShrinkingStairs": bool(shrinking),
        "stairDistances": stair_dists,
        "pressureScore": int(pressure),
        "consecutiveTrendBars": int(consec_trend),
        "shavedBarFrequency": float(shaved_freq),
        "vacuumMagnet": vacuum_name,
        "vacuumDistance": float(vacuum_dist),
        "isBreakoutTest": bool(bo_test),
        "breakoutTestLevel": bo_level,
        "gapBarWith2HM": bool(gap_2hm),
        "shavedBarLeg": int(shaved_leg),
        "shavedBarIsExhaustion": bool(shaved_exhaust),
        "alwaysInStatus": ais,
        "priorDayHigh": float(pdh),
        "priorDayLow": float(pdl),
        "openingGap": opening_gap,
        "spikeOriginPrice": spike_origin,
        "isTFO": bool(is_tfo),
        "tfoDirection": tfo_direction,
        "isM2B": bool(is_m2b),
        "isM2S": bool(is_m2s),
        "testOfExtreme": test_extreme_type,
        "testOfExtremeComplete": bool(test_extreme_complete),
        "channelLineBreak": channel_break,
        "pdInteraction": pd_interaction,
        "pdProximity": float(pd_proximity),
        "trVolatility": tr_vol,
        "trendlineSignificance": tl_significance,
        "barbWireLocation": bw_location,
        "isClimaticOutlier": bool(is_climactic),
        "climaticOutlierType": climactic_type,
        "isFiveTickFailure": bool(five_tick),
        "vacuumVelocity": int(vacuum_vel),
        "gapBarPressure": int(gap_bar_prs),
    }

    # Brooks Score with real data (new 5-component scoring with decomposition)
    score, score_decomposition = compute_brooks_score(
        trade, df, ema_values, swings, entry_idx,
        all_trades, trade_idx, flags,
    )

    # Trade quality classification
    trade_quality = classify_trade_quality(score["total"], trade["pnl_points"])

    # Mistake tag
    flags["_score_total"] = score["total"]
    mistake_tag = assign_mistake_tag(flags, trade, all_trades, trade_idx, score["context"])
    del flags["_score_total"]

    # Thesis / Reality
    thesis = generate_thesis(flags, trade)
    reality = generate_reality(flags, trade)

    # Profit left on table: MFE - abs(pnlPoints)
    profit_left = float(mfe - abs(trade["pnl_points"]))

    # MAE as stop percentage: MAE / signal_bar_range (or entry_price * 0.001)
    signal_bar_range = 0.0
    if entry_candle is not None:
        signal_bar_range = float(entry_candle["high"] - entry_candle["low"])
    denominator = signal_bar_range if signal_bar_range > 0 else (trade["entry_price"] * 0.001)
    mae_as_stop_pct = float((mae / denominator) * 100) if denominator > 0 else 0.0

    # Capture Ratio: |pnl| / MFE — how much of the move was captured (0-1)
    capture_ratio = float(abs(trade["pnl_points"]) / mfe) if mfe > 0 else 0.0
    capture_ratio = min(1.0, capture_ratio)

    # Reward-to-Risk: MFE / initial_risk (signal bar range)
    initial_risk = signal_bar_range if signal_bar_range > 0 else (trade["entry_price"] * 0.001)
    reward_to_risk = float(mfe / initial_risk) if initial_risk > 0 else 0.0

    analogy = pick_analogy(flags)

    # EMA at entry for the 2HM gauge
    ema_at_entry = ema_values[entry_idx] if entry_idx < len(ema_values) else trade["entry_price"]

    # Session reference: exit index and MFE/MAE bar indices
    session_key = f"{trade['symbol']}|{trade['entry_time'].split(' ')[0]}"

    exit_candle = find_candle_at_time(df, trade["exit_time"])
    exit_idx = 0
    if exit_candle is not None and len(df) > 0:
        match = df[df["timestamp"] == exit_candle["timestamp"]]
        if len(match) > 0:
            exit_idx = int(match.index[0])

    trade_candles_for_idx = find_candles_between(df, trade["entry_time"], trade["exit_time"])
    mfe_bar_idx = 0
    mae_bar_idx = 0
    if len(trade_candles_for_idx) > 0:
        if is_long:
            mfe_bar_idx = int(trade_candles_for_idx["high"].idxmax())
            mae_bar_idx = int(trade_candles_for_idx["low"].idxmin())
        else:
            mfe_bar_idx = int(trade_candles_for_idx["low"].idxmin())
            mae_bar_idx = int(trade_candles_for_idx["high"].idxmax())

    # Build candle data for sparkline (actual candles during trade)
    trade_candles = find_candles_between(df, trade["entry_time"], trade["exit_time"])
    sparkline_data = []
    for _, c in trade_candles.iterrows():
        sparkline_data.append({
            "t": c["timestamp"].strftime("%H:%M"),
            "o": float(c["open"]),
            "h": float(c["high"]),
            "l": float(c["low"]),
            "c": float(c["close"]),
        })

    return {
        "tradeNum": trade["trade_num"],
        "symbol": trade["symbol"],
        "side": trade["side"],
        "entryTime": trade["entry_time"],
        "exitTime": trade["exit_time"],
        "qty": trade["qty"],
        "entryPrice": trade["entry_price"],
        "exitPrice": trade["exit_price"],
        "pnlPoints": trade["pnl_points"],
        "pnlRupees": trade["pnl_rupees"],
        "duration": trade["duration"],
        "sessionKey": session_key,
        "entryIdx": int(entry_idx),
        "exitIdx": int(exit_idx),
        "mfeBarIdx": int(mfe_bar_idx),
        "maeBarIdx": int(mae_bar_idx),
        "metrics": {
            "mfe": float(mfe),
            "mae": float(mae),
            "mfePct": float(mfe / trade["entry_price"] * 100) if trade["entry_price"] else 0,
            "maePct": float(mae / trade["entry_price"] * 100) if trade["entry_price"] else 0,
        },
        "score": score,
        "scoreDecomposition": score_decomposition,
        "flags": flags,
        "tradeQuality": trade_quality,
        "mistakeTag": mistake_tag,
        "thesis": thesis,
        "reality": reality,
        "profitLeftOnTable": round(profit_left, 2),
        "maeAsStopPct": round(mae_as_stop_pct, 2),
        "captureRatio": round(capture_ratio, 3),
        "rewardToRisk": round(reward_to_risk, 2),
        "analogy": analogy,
        "signalBar": signal_bar_info,
        "emaAtEntry": float(ema_at_entry),
        "sparklineCandles": sparkline_data,
        "dataSource": "real",
    }


def run():
    print("=" * 70)
    print("  TRADE INTEGRITY PIPELINE")
    print("  Real Market Data Fetcher + Brooks PA Analyzer")
    print("=" * 70)
    print()

    # 1. Parse trades
    print("Step 1: Parsing trades CSV...")
    trades = parse_trades_csv(CSV_PATH)
    print(f"  Found {len(trades)} trades\n")

    # 2. Fetch candle data
    print("Step 2: Fetching 5-minute OHLC candle data...")
    all_candles = fetch_all_candles(trades)
    print()

    # 3. Analyze each trade
    print("Step 3: Running Brooks Price Action analysis with real data...")
    enriched = []

    # Pre-compute EMA and swings for each day/symbol
    day_analysis = {}
    for key, candles in all_candles.items():
        df = candles_to_df(candles)
        if len(df) == 0:
            day_analysis[key] = (df, [], [])
            continue
        closes = df["close"].tolist()
        ema_values = compute_ema(closes, EMA_PERIOD)
        swings = find_swing_points(df)
        day_analysis[key] = (df, ema_values, swings)

    # Build sessions object (shared chart data per symbol+date)
    print("\n  Building sessions object for chart data...")
    sessions = {}
    for key, (df, ema_values, swings) in day_analysis.items():
        if len(df) == 0:
            continue
        parts = key.split("|")
        symbol = parts[0]
        trade_date = parts[1]
        pdh, pdl, og = compute_prior_day_levels(all_candles, symbol, trade_date)
        sessions[key] = {
            "candles": [
                {
                    "t": row["timestamp"].strftime("%H:%M") if hasattr(row["timestamp"], "strftime") else str(row["timestamp"])[-8:-3],
                    "o": round(float(row["open"]), 2),
                    "h": round(float(row["high"]), 2),
                    "l": round(float(row["low"]), 2),
                    "c": round(float(row["close"]), 2),
                }
                for _, row in df.iterrows()
            ],
            "ema": [round(float(v), 2) for v in ema_values],
            "swingPoints": [
                {
                    "idx": int(s[0]),
                    "type": s[1],
                    "price": round(float(s[2]), 2),
                    "time": df.iloc[s[0]]["timestamp"].strftime("%H:%M") if s[0] < len(df) and hasattr(df.iloc[s[0]]["timestamp"], "strftime") else "",
                }
                for s in swings
            ],
            "barbWireZones": detect_barb_wire_zones(df),
            "trendlines": compute_session_trendlines(df, swings),
            "priorDayHigh": float(pdh),
            "priorDayLow": float(pdl),
            "openingGap": og,
            "marketPhaseRegions": detect_market_phase_regions(df, ema_values),
        }
    print(f"  Built {len(sessions)} sessions ({sum(len(s['candles']) for s in sessions.values())} total candles)")

    for idx, trade in enumerate(trades):
        day_key = f"{trade['symbol']}|{trade['entry_time'].split(' ')[0]}"
        df, ema_values, swings = day_analysis.get(day_key, (pd.DataFrame(), [], []))

        if len(df) == 0:
            print(f"  ⚠ Trade #{trade['trade_num']}: No candle data — using CSV-only fallback")
            _empty_decomp = {
                "context": {"score": 0, "reasons": ["No candle data"]},
                "setupReliability": {"score": 0, "reasons": ["No candle data"]},
                "signalBar": {"score": 0, "reasons": ["No candle data"]},
                "institutional": {"score": 0, "reasons": ["No candle data"]},
                "pressure": {"score": 0, "reasons": ["No candle data"]},
            }
            _fb_quality = "BAD_LOSS" if trade["pnl_points"] <= 0 else "BAD_WIN"
            enriched.append({
                "tradeNum": trade["trade_num"],
                "symbol": trade["symbol"],
                "side": trade["side"],
                "entryTime": trade["entry_time"],
                "exitTime": trade["exit_time"],
                "qty": trade["qty"],
                "entryPrice": trade["entry_price"],
                "exitPrice": trade["exit_price"],
                "pnlPoints": trade["pnl_points"],
                "pnlRupees": trade["pnl_rupees"],
                "duration": trade["duration"],
                "sessionKey": day_key,
                "entryIdx": 0,
                "exitIdx": 0,
                "mfeBarIdx": 0,
                "maeBarIdx": 0,
                "metrics": {"mfe": 0, "mae": 0, "mfePct": 0, "maePct": 0},
                "score": {"total": 0, "context": 0, "setupReliability": 0, "signalBar": 0, "institutional": 0, "pressure": 0},
                "scoreDecomposition": _empty_decomp,
                "flags": {
                    "isBarbWire": False, "twoHM": 0, "isShavedBar": False,
                    "isGapBar": False, "leg": 1, "isFailedFailure": False,
                    "isInstitutionalTrap": False, "isStopRun": False,
                    "isMiddleOfMiddle": False, "withTrend": False,
                    "reachedMM": False, "measuredMoveTarget": 0, "pnl": trade["pnl_rupees"],
                    "marketPhase": "TRADING_RANGE", "emaSlope": 0.0,
                    "emaSlopeClassification": "FLAT", "trendlineIntact": True,
                    "isFailedFinalFlag": False, "isFunctionalLeg": False,
                    "functionalLegType": None, "isShrinkingStairs": False,
                    "stairDistances": [], "pressureScore": 0,
                    "consecutiveTrendBars": 0, "shavedBarFrequency": 0.0,
                    "vacuumMagnet": None, "vacuumDistance": 0.0,
                    "isBreakoutTest": False, "breakoutTestLevel": None,
                    "gapBarWith2HM": False, "shavedBarLeg": 0,
                    "shavedBarIsExhaustion": False, "alwaysInStatus": "NEUTRAL",
                    "priorDayHigh": 0.0, "priorDayLow": 0.0,
                    "openingGap": None, "spikeOriginPrice": None,
                    "isTFO": False, "tfoDirection": None,
                    "isM2B": False, "isM2S": False,
                    "testOfExtreme": None, "testOfExtremeComplete": False,
                    "channelLineBreak": None,
                    "pdInteraction": None, "pdProximity": 0.0,
                    "trVolatility": None, "trendlineSignificance": None,
                    "barbWireLocation": None, "isClimaticOutlier": False,
                    "climaticOutlierType": None, "isFiveTickFailure": False,
                    "vacuumVelocity": 0, "gapBarPressure": 0,
                },
                "tradeQuality": _fb_quality,
                "mistakeTag": "",
                "thesis": "No candle data — cannot generate thesis.",
                "reality": f"{'Won' if trade['pnl_points'] > 0 else 'Lost'} {abs(trade['pnl_points']):.1f} pts.",
                "profitLeftOnTable": 0.0,
                "maeAsStopPct": 0.0,
                "captureRatio": 0.0,
                "rewardToRisk": 0.0,
                "analogy": "No candle data available — analysis requires real market data from your broker API.",
                "signalBar": None,
                "emaAtEntry": trade["entry_price"],
                "sparklineCandles": [],
                "dataSource": "csv_only",
            })
            continue

        result = analyze_trade(trade, df, ema_values, swings, trades, idx, all_candles)
        enriched.append(result)
        status = "✓" if result["dataSource"] == "real" else "⚠"
        print(f"  {status} Trade #{trade['trade_num']}: {trade['symbol']} {trade['side']} — Brooks {result['score']['total']}/10")

    # 4. Write output
    # Custom encoder to handle numpy types
    class NumpyEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.bool_,)):
                return bool(obj)
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, (np.ndarray,)):
                return obj.tolist()
            if isinstance(obj, (pd.Timestamp, datetime)):
                return obj.isoformat()
            return super().default(obj)

    now = datetime.now()
    real_count = sum(1 for t in enriched if t["dataSource"] == "real")

    # Determine date range from trades
    trade_dates = sorted(set(t["entry_time"].split(" ")[0] for t in trades))
    date_range = f"{trade_dates[0]}_to_{trade_dates[-1]}" if trade_dates else "unknown"

    output_payload = {
        "generatedAt": now.isoformat(),
        "broker": BROKER,
        "csvFile": str(CSV_PATH.name),
        "dateRange": date_range,
        "totalTrades": len(enriched),
        "realDataTrades": real_count,
        "sessions": sessions,
        "trades": enriched,
    }

    # ── Save to dashboard (latest, for live display) ──
    print(f"\nStep 4: Saving analysis...")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output_payload, f, indent=2, cls=NumpyEncoder)
    print(f"  → Dashboard: {OUTPUT_PATH}")

    # ── Save timestamped copy to analysis_history/ ──
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")
    history_file = HISTORY_DIR / f"analysis_{timestamp_str}_{date_range}.json"
    with open(history_file, "w") as f:
        json.dump(output_payload, f, indent=2, cls=NumpyEncoder)
    print(f"  → History:   {history_file}")

    # ── List all saved analyses ──
    all_history = sorted(HISTORY_DIR.glob("analysis_*.json"))
    print(f"\n  📂 analysis_history/ ({len(all_history)} saved runs):")
    for h in all_history:
        size_kb = h.stat().st_size / 1024
        print(f"     • {h.name}  ({size_kb:.0f} KB)")

    print(f"\n{'=' * 70}")
    print(f"  DONE! {real_count}/{len(enriched)} trades analyzed with real chart data")
    print(f"  Dashboard:  {OUTPUT_PATH}")
    print(f"  Saved copy: {history_file}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run()
