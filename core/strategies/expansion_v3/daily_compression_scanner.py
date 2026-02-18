"""
PixityAI v3 — Daily Compression Scanner
========================================
Runs after market close (EOD) for each trading day.
Produces a list of compression candidates with directional bias.

CAUSAL GUARANTEE:
    All computations use only data up to and including day T.
    No future data leaks. Compression is detected at close of T,
    and candidates are eligible for 1H trigger on day T+1 onwards.

FILTER PIPELINE (applied in order, cheapest first):

  1. Liquidity:   Median 20d daily traded value > Rs 10 crore
  2. ATR rank:    Stock ATR% (ATR/Close) ranked cross-sectionally across universe.
                  Keep stocks in the BOTTOM 30th percentile of universe ATR%.
                  This is rank-based and dynamic — no per-symbol history required.
                  Companion per-symbol check: stock's own ATR% below its own
                  120-day median (secondary confirmation, graceful with short history).
  3. Range comp:  20-day price span < 50% of 60-day price span (tightened from 70%).
  4. RS_5d:       Stock 5d return minus Nifty 5d return.
                  Long: RS > 0. Short: RS < 0.
  5. Structure:   Directionally aligned price proximity.
                  Long:  close >= 90% of 60d high  (within 10% of 60d high)
                  Short: close <= 110% of 60d low  (within 10% of 60d low)

OUTPUT per candidate:
    symbol, trading_symbol, date, direction,
    atr_pct_universe_rank, atr_pct, rs_5d,
    range_ratio, price_vs_structure, liquidity_cr, close, atr, daily_atr_pct
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import duckdb
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CompressionConfig:
    # Cross-sectional ATR rank threshold (bottom X% of universe = compressed)
    atr_universe_pct_threshold: float = 30.0
    # Per-symbol own-history ATR confirmation: stock ATR% must be below its own
    # rolling median over this many days (uses available history, not hard minimum)
    atr_own_history_days: int = 60
    # ATR calculation period
    atr_period: int = 20
    # Range compression: 20d span must be < this fraction of 60d span
    range_compression_ratio: float = 0.50
    # Price proximity to 60-day structure
    structure_proximity_long: float = 0.90    # close >= 90% of 60d high
    structure_proximity_short: float = 1.10  # close <= 110% of 60d low
    # Liquidity: median 20d daily traded value in Rs crore
    min_liquidity_cr: float = 10.0
    # RS lookback (trading days)
    rs_lookback: int = 5
    # Minimum days of history to evaluate a symbol
    min_history: int = 65    # 60d for range + buffer; cross-sectional ATR needs no history


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CompressionCandidate:
    symbol: str
    trading_symbol: str
    scan_date: date
    direction: str                  # 'LONG' or 'SHORT'
    atr_universe_rank: float        # Cross-sectional ATR% rank (0-100, lower = more compressed)
    atr_pct: float                  # ATR / Close (raw, not ranked)
    rs_5d: float                    # Stock 5d return - Nifty 5d return
    range_ratio: float              # 20d span / 60d span
    price_vs_structure: float       # close/60d_high (long) or close/60d_low (short)
    liquidity_cr: float
    close: float
    atr: float
    daily_atr_pct: float            # ATR / Close

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "trading_symbol": self.trading_symbol,
            "scan_date": self.scan_date.isoformat(),
            "direction": self.direction,
            "atr_universe_rank": round(self.atr_universe_rank, 2),
            "atr_pct": round(self.atr_pct * 100, 4),
            "rs_5d": round(self.rs_5d * 100, 4),
            "range_ratio": round(self.range_ratio, 4),
            "price_vs_structure": round(self.price_vs_structure, 4),
            "liquidity_cr": round(self.liquidity_cr, 2),
            "close": round(self.close, 2),
            "atr": round(self.atr, 4),
            "daily_atr_pct": round(self.daily_atr_pct * 100, 4),
        }


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

class DailyCompressionScanner:
    """
    Scans all F&O universe symbols for compression candidates.

    Key design decisions:
    - Cross-sectional ATR ranking: robust with limited per-symbol history
    - Range compression uses span-vs-span (not span vs avg daily range)
    - Structure filter is directionally aligned (long near high, short near low)
    - All computations are strictly causal (data up to scan_date only)

    Usage:
        scanner = DailyCompressionScanner(data_root="data", config=CompressionConfig())
        candidates = scanner.scan_date(date(2025, 8, 1))
        # or for a range:
        results = scanner.scan_range(date(2025, 7, 1), date(2026, 2, 13))
    """

    def __init__(
        self,
        data_root: str | Path,
        config: Optional[CompressionConfig] = None,
        symbol_map: Optional[Dict[str, str]] = None,
    ):
        self.data_root = Path(data_root)
        self.cfg = config or CompressionConfig()
        self.symbol_map = symbol_map or {}
        self._daily_cache: Dict[str, pd.DataFrame] = {}
        self._nifty_cache: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def scan_date(self, scan_date: date) -> List[CompressionCandidate]:
        """
        Run full compression scan for a single date.
        Returns candidate list (may be empty). Strictly causal.
        """
        # --- Nifty 5d return ---
        nifty_daily = self._get_nifty_daily(scan_date)
        if nifty_daily is None or len(nifty_daily) < self.cfg.rs_lookback + 1:
            logger.warning(f"Insufficient Nifty data for {scan_date}")
            return []
        nifty_5d_return = _compute_return(nifty_daily, self.cfg.rs_lookback)

        symbols = list(self.symbol_map.keys())

        # --- Pass 1: collect ATR% for all symbols (for cross-sectional ranking) ---
        symbol_metrics: Dict[str, dict] = {}
        for symbol in symbols:
            df = self._get_symbol_daily(symbol, scan_date)
            if df is None or len(df) < self.cfg.min_history:
                continue
            close = df["close"].iloc[-1]
            if close <= 0:
                continue
            # Liquidity gate (cheapest rejection first)
            traded_val = (df["close"] * df["volume"]).tail(20).median() / 1e7
            if traded_val < self.cfg.min_liquidity_cr:
                continue
            atr_series = _compute_atr(df, self.cfg.atr_period)
            if atr_series is None or atr_series.iloc[-1] <= 0:
                continue
            current_atr = atr_series.iloc[-1]
            atr_pct = current_atr / close   # normalised volatility
            symbol_metrics[symbol] = {
                "df": df,
                "close": close,
                "atr": current_atr,
                "atr_pct": atr_pct,
                "atr_series": atr_series,
                "liquidity_cr": traded_val,
            }

        if not symbol_metrics:
            return []

        # --- Cross-sectional ATR% ranking ---
        all_atr_pcts = np.array([m["atr_pct"] for m in symbol_metrics.values()])
        for sym, m in symbol_metrics.items():
            rank = float((all_atr_pcts < m["atr_pct"]).sum() / len(all_atr_pcts) * 100)
            m["atr_universe_rank"] = rank

        # --- Pass 2: apply remaining filters ---
        candidates = []
        for symbol, m in symbol_metrics.items():
            # Filter 2a: cross-sectional ATR rank — bottom 30% of universe
            if m["atr_universe_rank"] >= self.cfg.atr_universe_pct_threshold:
                continue

            # Filter 2b: own-history ATR confirmation (secondary, graceful)
            atr_series = m["atr_series"]
            own_window = atr_series.tail(self.cfg.atr_own_history_days)
            if len(own_window) >= 20:
                own_median_atr_pct = (own_window / m["df"]["close"].tail(len(own_window))).median()
                if m["atr_pct"] > own_median_atr_pct:
                    continue  # ATR expanding vs own recent history

            df = m["df"]
            highs = df["high"]
            lows = df["low"]
            close = m["close"]

            # Filter 3: range compression — 20d span < 50% of 60d span
            range_20 = highs.tail(20).max() - lows.tail(20).min()
            range_60 = highs.tail(60).max() - lows.tail(60).min()
            if range_60 <= 0:
                continue
            range_ratio = range_20 / range_60
            if range_ratio >= self.cfg.range_compression_ratio:
                continue

            # Filter 4: RS_5d directional alignment
            rs_5d = _compute_return(df, self.cfg.rs_lookback) - nifty_5d_return
            if rs_5d == 0:
                continue

            # Filter 5: structure proximity (directionally aligned)
            high_60 = highs.tail(60).max()
            low_60 = lows.tail(60).min()

            direction = None
            price_vs_structure = 0.0

            if rs_5d > 0:
                ratio = close / high_60 if high_60 > 0 else 0.0
                if ratio >= self.cfg.structure_proximity_long:
                    direction = "LONG"
                    price_vs_structure = ratio
            else:
                ratio = close / low_60 if low_60 > 0 else 9.9
                if ratio <= self.cfg.structure_proximity_short:
                    direction = "SHORT"
                    price_vs_structure = ratio

            if direction is None:
                continue

            trading_symbol = self.symbol_map.get(symbol, symbol.split("|")[-1])
            candidates.append(CompressionCandidate(
                symbol=symbol,
                trading_symbol=trading_symbol,
                scan_date=scan_date,
                direction=direction,
                atr_universe_rank=m["atr_universe_rank"],
                atr_pct=m["atr_pct"],
                rs_5d=rs_5d,
                range_ratio=range_ratio,
                price_vs_structure=price_vs_structure,
                liquidity_cr=m["liquidity_cr"],
                close=close,
                atr=m["atr"],
                daily_atr_pct=m["atr_pct"],
            ))

        return candidates

    def scan_range(
        self,
        start_date: date,
        end_date: date,
        trading_days: Optional[List[date]] = None,
    ) -> Dict[date, List[CompressionCandidate]]:
        """
        Run scan across a date range. Preloads all data once for performance.
        Returns dict: {date -> [candidates]}.
        """
        if trading_days is None:
            trading_days = self._get_trading_days(start_date, end_date)

        logger.info(f"Scanning {len(trading_days)} trading days "
                    f"from {start_date} to {end_date}...")

        # Preload: go back far enough for all indicators
        preload_from = start_date - timedelta(days=200)
        logger.info("Pre-loading daily OHLCV series for all symbols...")
        self._preload_all_symbols(preload_from, end_date)

        results: Dict[date, List[CompressionCandidate]] = {}
        for td in trading_days:
            candidates = self.scan_date(td)
            results[td] = candidates
            n_long = sum(1 for c in candidates if c.direction == 'LONG')
            n_short = sum(1 for c in candidates if c.direction == 'SHORT')
            logger.info(
                f"  {td} | Long: {n_long:3d} | Short: {n_short:3d} | Total: {len(candidates):3d}"
            )

        return results

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _get_symbol_daily(self, symbol: str, up_to_date: date) -> Optional[pd.DataFrame]:
        """Return causal daily OHLCV slice from cache."""
        if symbol not in self._daily_cache:
            return None
        df = self._daily_cache[symbol]
        sliced = df[df["date"] <= up_to_date]
        return sliced.copy() if len(sliced) >= 10 else None

    def _preload_all_symbols(self, from_date: date, to_date: date) -> None:
        """
        Aggregate 1m -> daily OHLCV for all symbols across all trading days.
        Opens each daily DuckDB file once and extracts all symbols in one query.
        """
        symbols_set = set(self.symbol_map.keys())
        m1_dir = self.data_root / "market_data" / "nse" / "candles" / "1m"

        trading_days = self._get_trading_days(from_date, to_date)
        # Equity data only available from 2025-01-01
        trading_days = [td for td in trading_days if td >= date(2025, 1, 1)]

        logger.info(f"Aggregating {len(trading_days)} days of 1m data to daily for "
                    f"{len(symbols_set)} symbols...")

        symbol_rows: Dict[str, List[dict]] = {s: [] for s in symbols_set}

        for td in trading_days:
            db_path = m1_dir / f"{td.isoformat()}.duckdb"
            if not db_path.exists():
                continue
            try:
                conn = duckdb.connect(str(db_path), read_only=True)
                df = conn.execute("""
                    SELECT
                        symbol,
                        FIRST(open  ORDER BY timestamp) AS open,
                        MAX(high)                        AS high,
                        MIN(low)                         AS low,
                        LAST(close  ORDER BY timestamp) AS close,
                        SUM(volume)                      AS volume
                    FROM candles
                    WHERE timeframe = '1m'
                    GROUP BY symbol
                """).df()
                conn.close()
                for _, row in df.iterrows():
                    sym = row["symbol"]
                    if sym in symbol_rows:
                        symbol_rows[sym].append({
                            "date": td,
                            "open":   float(row["open"]),
                            "high":   float(row["high"]),
                            "low":    float(row["low"]),
                            "close":  float(row["close"]),
                            "volume": float(row["volume"]),
                        })
            except Exception as e:
                logger.debug(f"Error loading {td}: {e}")

        for sym, rows in symbol_rows.items():
            if rows:
                self._daily_cache[sym] = (
                    pd.DataFrame(rows)
                    .sort_values("date")
                    .reset_index(drop=True)
                )

        self._preload_nifty(from_date, to_date)

        loaded = sum(1 for v in self._daily_cache.values() if len(v) > 0)
        logger.info(f"Preloaded {loaded}/{len(symbols_set)} symbols successfully.")

    def _preload_nifty(self, from_date: date, to_date: date) -> None:
        """Load Nifty 50 daily OHLCV from the 1d DuckDB files."""
        d1_dir = self.data_root / "market_data" / "nse" / "candles" / "1d"
        rows = []
        current = from_date
        while current <= to_date:
            db_path = d1_dir / f"{current.isoformat()}.duckdb"
            if db_path.exists():
                try:
                    conn = duckdb.connect(str(db_path), read_only=True)
                    df = conn.execute("""
                        SELECT open, high, low, close, volume FROM candles
                        WHERE symbol = 'NSE_INDEX|Nifty 50' LIMIT 1
                    """).df()
                    conn.close()
                    if not df.empty:
                        rows.append({
                            "date":   current,
                            "open":   float(df["open"].iloc[0]),
                            "high":   float(df["high"].iloc[0]),
                            "low":    float(df["low"].iloc[0]),
                            "close":  float(df["close"].iloc[0]),
                            "volume": float(df["volume"].iloc[0]),
                        })
                except Exception:
                    pass
            current += timedelta(days=1)

        if rows:
            self._nifty_cache = (
                pd.DataFrame(rows)
                .sort_values("date")
                .reset_index(drop=True)
            )

    def _get_nifty_daily(self, up_to_date: date) -> Optional[pd.DataFrame]:
        """Return causal Nifty daily slice."""
        if self._nifty_cache is None:
            self._preload_nifty(up_to_date - timedelta(days=30), up_to_date)
        if self._nifty_cache is None:
            return None
        sliced = self._nifty_cache[self._nifty_cache["date"] <= up_to_date]
        return sliced if len(sliced) > 0 else None

    def _get_trading_days(self, from_date: date, to_date: date) -> List[date]:
        """Detect trading days from existing 1m DuckDB files (ground truth)."""
        m1_dir = self.data_root / "market_data" / "nse" / "candles" / "1m"
        days = []
        current = from_date
        while current <= to_date:
            if (m1_dir / f"{current.isoformat()}.duckdb").exists():
                days.append(current)
            current += timedelta(days=1)
        return sorted(days)


# ---------------------------------------------------------------------------
# Module-level indicator functions (no class dependency, easily testable)
# ---------------------------------------------------------------------------

def _compute_atr(df: pd.DataFrame, period: int = 20) -> Optional[pd.Series]:
    """Wilder's ATR."""
    if len(df) < period + 1:
        return None
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _compute_return(df: pd.DataFrame, lookback: int) -> float:
    """Simple price return over lookback bars."""
    if len(df) <= lookback:
        return 0.0
    past  = df["close"].iloc[-(lookback + 1)]
    curr  = df["close"].iloc[-1]
    return (curr - past) / past if past > 0 else 0.0
