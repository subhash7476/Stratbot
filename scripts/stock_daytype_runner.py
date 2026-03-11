#!/usr/bin/env python3
"""
Stock Day-Type Paper Trading Runner
-------------------------------------
Background thread that:
  1. Loads all tracked Nifty 50 equity symbols from stocks_universal_labels.csv
  2. Resolves trading_symbol names from the config DB (fo_stocks / instrument_meta)
  3. Polls the live buffer (candles_today.duckdb) every ~30 s
  4. Feeds new 1-minute bars to StockDaytypePaperStrategy
  5. Handles session resets at EOD

Checkpoint: 10:00 AM (bar 45) — classifies and enters at 10:01 AM
Exit: 15:28 PM (bar 374) or stop-hit

Run from unified_runner.py as a daemon thread.
Can also be run standalone for testing:
    python scripts/stock_daytype_runner.py
"""
import sys
import time
import logging
import threading
from datetime import datetime, date, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database.manager import DatabaseManager
from core.database.utils.market_hours import MarketHours
from core.strategies.stock_daytype_paper import StockDaytypePaperStrategy, BROKERS
from core.logging import setup_logger

logger = setup_logger("stock_daytype_runner")

# ── Paths ──────────────────────────────────────────────────────────────────
LABELS_CSV  = ROOT / "data" / "features" / "day_type" / "stocks_universal_labels.csv"
MODEL_DIR   = ROOT / "models" / "daytype" / "stock_1000am"
POLL_SECS   = 30   # live buffer polling interval during market hours


class StockDaytypeRunner:
    """
    Continuously polls the live buffer and feeds 1m bars to the paper
    trading strategy. Thread-safe; designed to run as a daemon thread.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        broker: str = "paper",
        stop_pct: float = 0.01,
        target_pct: float = 0.02,
        max_capital: float = 50000.0,
        min_confidence: float = 0.50,
        max_open_positions: int = 10,
    ):
        self.db                 = db_manager
        self.broker             = broker
        self.stop_pct           = stop_pct
        self.target_pct         = target_pct
        self.max_capital        = max_capital
        self.min_confidence     = min_confidence
        self.max_open_positions = max_open_positions

        # Load symbols and resolve human-readable names
        self.symbols      = self._load_symbols()
        self.symbol_names = self._resolve_names(self.symbols)

        # Instantiate strategy
        self.strategy = StockDaytypePaperStrategy(
            db_manager         = db_manager,
            model_dir          = MODEL_DIR,
            symbols            = self.symbols,
            symbol_names       = self.symbol_names,
            broker             = broker,
            stop_pct           = stop_pct,
            target_pct         = target_pct,
            max_capital        = max_capital,
            min_confidence     = min_confidence,
            max_open_positions = max_open_positions,
        )

        # Last-processed timestamp per symbol (to avoid re-processing same bars)
        self._last_ts: Dict[str, Optional[datetime]] = {s: None for s in self.symbols}
        # The trading date for which bars have been processed (reset each new day)
        self._session_date: Optional[date] = None

    # ──────────────────────────────────────────────────────────────────────
    #  Public API
    # ──────────────────────────────────────────────────────────────────────

    def run(self, stop_event: threading.Event) -> None:
        """Main loop — call this in a background thread."""
        logger.info(
            f"[PaperRunner] Started | {len(self.symbols)} symbols | "
            f"broker={self.broker} | model={MODEL_DIR.name}"
        )

        while not stop_event.is_set():
            now   = MarketHours.get_ist_now()
            today = now.date()

            if MarketHours.is_market_open(now):
                # New trading day — reset bar tracking once at session open
                if self._session_date != today:
                    logger.info(f"[PaperRunner] New session: {today} — resetting bar tracking.")
                    self._last_ts     = {s: None for s in self.symbols}
                    self._session_date = today
                self._process_new_bars()
            else:
                # Market closed — sleep silently, no resets, no processing
                pass

            stop_event.wait(timeout=POLL_SECS)

        logger.info("[PaperRunner] Stopped.")

    def set_broker(self, broker: str) -> bool:
        """Change broker at runtime (affects subsequent trades)."""
        ok = self.strategy.set_broker(broker)
        if ok:
            self.broker = broker
        return ok

    # ──────────────────────────────────────────────────────────────────────
    #  Private helpers
    # ──────────────────────────────────────────────────────────────────────

    def _process_new_bars(self) -> None:
        zero_bar_count = 0
        for symbol in self.symbols:
            try:
                new_bars = self._fetch_new_bars(symbol)
                for bar in new_bars:
                    self.strategy.on_bar(symbol, bar)
                if new_bars:
                    self._last_ts[symbol] = new_bars[-1]["timestamp"]
                else:
                    zero_bar_count += 1
            except Exception as exc:
                logger.debug(f"[PaperRunner] {symbol}: {exc}")
                zero_bar_count += 1

        if self.symbols and zero_bar_count == len(self.symbols):
            logger.warning(
                "[PaperRunner] All %d symbols returned 0 bars — "
                "live buffer may be empty or locked (check ingestor / Windows Defender)",
                len(self.symbols),
            )

    def _fetch_new_bars(self, symbol: str) -> List[dict]:
        """Read new 1m candles from the live buffer since last processed timestamp."""
        last_ts = self._last_ts.get(symbol)
        try:
            with self.db.live_buffer_reader() as conns:
                if "candles" not in conns:
                    return []
                conn = conns["candles"]
                if last_ts is None:
                    # New session: only fetch TODAY's bars.
                    # Without this filter, stale yesterday bars in the live buffer
                    # cause oscillating _reset_session calls across symbols,
                    # entering/exiting positions with wrong-day data.
                    today_open = datetime.combine(date.today(), dt_time(9, 0, 0))
                    rows = conn.execute(
                        """
                        SELECT timestamp, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = ? AND timeframe = '1m' AND timestamp >= ?
                        ORDER BY timestamp ASC
                        """,
                        [symbol, today_open],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT timestamp, open, high, low, close, volume
                        FROM candles
                        WHERE symbol = ? AND timeframe = '1m' AND timestamp > ?
                        ORDER BY timestamp ASC
                        """,
                        [symbol, last_ts],
                    ).fetchall()

                return [
                    {
                        "timestamp": r[0],
                        "open":      float(r[1]),
                        "high":      float(r[2]),
                        "low":       float(r[3]),
                        "close":     float(r[4]),
                        "volume":    int(r[5]) if r[5] else 0,
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.debug(f"[PaperRunner] Fetch failed for {symbol}: {exc}")
            return []

    # ──────────────────────────────────────────────────────────────────────
    #  Startup helpers
    # ──────────────────────────────────────────────────────────────────────

    @staticmethod
    def _load_symbols() -> List[str]:
        """Load the unique equity symbols from stocks_universal_labels.csv."""
        if not LABELS_CSV.exists():
            logger.warning(f"[PaperRunner] Labels CSV not found: {LABELS_CSV}")
            return []
        try:
            seen   = set()
            result = []
            with open(LABELS_CSV, "r") as fh:
                header = fh.readline()   # skip header
                col_idx = header.strip().split(",").index("symbol")
                for line in fh:
                    parts = line.strip().split(",")
                    if len(parts) > col_idx:
                        sym = parts[col_idx]
                        if sym and sym not in seen:
                            seen.add(sym)
                            result.append(sym)
            logger.info(f"[PaperRunner] Loaded {len(result)} symbols from labels CSV")
            return result
        except Exception as exc:
            logger.error(f"[PaperRunner] Failed to load symbols: {exc}")
            return []

    def _resolve_names(self, symbols: List[str]) -> Dict[str, str]:
        """
        Resolve instrument_key -> trading_symbol from config DB.
        Falls back to the last segment of the instrument key for unknowns.
        """
        names = {}
        # Default: use ISIN portion of the key
        for sym in symbols:
            names[sym] = sym.split("|")[-1]

        try:
            with self.db.config_reader() as conn:
                # Try fo_stocks first (has equity names like RELIANCE)
                rows = conn.execute(
                    "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
                ).fetchall()
                for ikey, tsym in rows:
                    if ikey in names:
                        names[ikey] = tsym

                # Also try instrument_meta
                rows2 = conn.execute(
                    "SELECT instrument_key, trading_symbol FROM instrument_meta"
                ).fetchall()
                for ikey, tsym in rows2:
                    if ikey in names and tsym:
                        names[ikey] = tsym
        except Exception as exc:
            logger.debug(f"[PaperRunner] Could not resolve names from DB: {exc}")

        resolved = sum(1 for s in symbols if names.get(s, s.split("|")[-1]) != s.split("|")[-1])
        logger.info(f"[PaperRunner] Resolved {resolved}/{len(symbols)} trading symbols from DB")
        return names


# ── Standalone entry point ─────────────────────────────────────────────────
if __name__ == "__main__":
    data_root  = ROOT / "data"
    db_manager = DatabaseManager(data_root)
    stop_ev    = threading.Event()

    runner = StockDaytypeRunner(db_manager, broker="paper")
    try:
        runner.run(stop_ev)
    except KeyboardInterrupt:
        stop_ev.set()
        logger.info("StockDaytypeRunner stopped by user.")
