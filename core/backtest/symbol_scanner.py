"""
Symbol Scanner — Walk-Forward Validation Across All Symbols
-----------------------------------------------------------
Automates train/test backtest runs for every symbol in the universe,
ranks by profitability, and computes correlation for portfolio construction.
"""
import uuid
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Callable, Any
from pathlib import Path

from core.backtest.runner import BacktestRunner
from core.database.manager import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass
class SymbolResult:
    """Walk-forward results for a single symbol."""
    symbol: str
    trading_symbol: str

    # Train period metrics
    train_pnl: float = 0.0
    train_trades: int = 0
    train_win_rate: float = 0.0
    train_max_dd: float = 0.0
    train_run_id: str = ""
    train_status: str = "PENDING"

    # Test period metrics
    test_pnl: float = 0.0
    test_trades: int = 0
    test_win_rate: float = 0.0
    test_max_dd: float = 0.0
    test_run_id: str = ""
    test_status: str = "PENDING"

    # Derived
    is_profitable: bool = False
    rank: int = 0
    error: str = ""


@dataclass
class ScanResults:
    """Aggregated results from a full symbol scan."""
    scan_id: str
    timestamp: datetime
    total_symbols: int
    symbol_results: List[SymbolResult] = field(default_factory=list)
    profitable_symbols: int = 0
    scan_params: Dict[str, Any] = field(default_factory=dict)
    status: str = "RUNNING"


# Profitability criteria thresholds
DEFAULT_CRITERIA = {
    "min_test_pnl": 0.0,
    "min_test_win_rate": 40.0,   # 40% WR with +PnL = strong R:R edge
    "min_train_pnl": 0.0,       # Train consistency
    "max_drawdown": 20.0,        # Max DD in either period
    "min_test_trades": 20,       # Statistical significance
}


class SymbolScanner:
    """Walk-forward scanner that validates the baseline strategy across all symbols."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.runner = BacktestRunner(db_manager)

    def get_all_equity_symbols(self) -> List[Dict[str, str]]:
        """Fetch all active equity symbols from config DB.

        Returns list of dicts with 'instrument_key' and 'trading_symbol'.
        """
        symbols = []
        try:
            with self.db.config_reader() as conn:
                rows = conn.execute(
                    "SELECT instrument_key, trading_symbol FROM fo_stocks WHERE is_active = 1"
                ).fetchall()
                for r in rows:
                    symbols.append({"instrument_key": r[0], "trading_symbol": r[1] or r[0]})
        except Exception as e:
            logger.error(f"Failed to load symbols from fo_stocks: {e}")

        if not symbols:
            logger.warning("No symbols from fo_stocks, falling back to instrument_meta NSE_EQ")
            try:
                with self.db.config_reader() as conn:
                    rows = conn.execute(
                        "SELECT instrument_key, trading_symbol FROM instrument_meta "
                        "WHERE instrument_key LIKE 'NSE_EQ%' AND is_active = 1"
                    ).fetchall()
                    for r in rows:
                        symbols.append({"instrument_key": r[0], "trading_symbol": r[1] or r[0]})
            except Exception as e:
                logger.error(f"Failed to load symbols from instrument_meta: {e}")

        logger.info(f"Loaded {len(symbols)} equity symbols")
        return symbols

    def scan_all_symbols(
        self,
        symbols: List[Dict[str, str]],
        train_start: datetime,
        train_end: datetime,
        test_start: datetime,
        test_end: datetime,
        initial_capital: float = 100000.0,
        timeframe: str = "15m",
        strategy_params: Optional[Dict] = None,
        criteria: Optional[Dict] = None,
        progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
    ) -> ScanResults:
        """Run walk-forward validation on every symbol.

        Args:
            symbols: List of dicts with 'instrument_key' and 'trading_symbol'.
            train_start/train_end: Training period boundaries.
            test_start/test_end: Test period boundaries.
            initial_capital: Starting capital per backtest.
            timeframe: Bar timeframe (default '15m').
            strategy_params: Override params (default: baseline, no filter, no model).
            criteria: Profitability criteria dict (see DEFAULT_CRITERIA).
            progress_callback: Called with (current_idx, total, symbol, status_msg).

        Returns:
            ScanResults with ranked symbol results.
        """
        scan_id = f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        criteria = criteria or DEFAULT_CRITERIA

        # Default to baseline config
        if strategy_params is None:
            strategy_params = {
                "skip_meta_model": True,
                "use_signal_quality_filter": False,
            }

        scan = ScanResults(
            scan_id=scan_id,
            timestamp=datetime.now(),
            total_symbols=len(symbols),
            scan_params={
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "initial_capital": initial_capital,
                "timeframe": timeframe,
                "strategy_params": strategy_params,
                "criteria": criteria,
            },
        )

        logger.info(f"Starting scan {scan_id}: {len(symbols)} symbols, "
                     f"train={train_start.date()}->{train_end.date()}, "
                     f"test={test_start.date()}->{test_end.date()}, tf={timeframe}")

        for idx, sym_info in enumerate(symbols):
            instrument_key = sym_info["instrument_key"]
            trading_symbol = sym_info.get("trading_symbol", instrument_key)

            if progress_callback:
                progress_callback(idx, len(symbols), trading_symbol, "starting")

            result = self._run_single_symbol(
                instrument_key=instrument_key,
                trading_symbol=trading_symbol,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                initial_capital=initial_capital,
                timeframe=timeframe,
                strategy_params=strategy_params,
                scan_id=scan_id,
            )

            # Apply profitability criteria
            result.is_profitable = self._check_profitability(result, criteria)
            scan.symbol_results.append(result)

            status = "profitable" if result.is_profitable else (
                "unprofitable" if not result.error else f"error: {result.error[:50]}"
            )
            if progress_callback:
                progress_callback(idx + 1, len(symbols), trading_symbol, status)

            logger.info(
                f"[{idx+1}/{len(symbols)}] {trading_symbol}: "
                f"Train PnL=Rs {result.train_pnl:,.0f} ({result.train_trades} trades), "
                f"Test PnL=Rs {result.test_pnl:,.0f} ({result.test_trades} trades) "
                f"-> {'PROFITABLE' if result.is_profitable else 'skip'}"
            )

        # Rank profitable symbols by test PnL
        self._rank_results(scan)
        scan.profitable_symbols = sum(1 for r in scan.symbol_results if r.is_profitable)
        scan.status = "COMPLETED"

        logger.info(
            f"Scan {scan_id} complete: {scan.profitable_symbols}/{scan.total_symbols} "
            f"symbols profitable"
        )

        return scan

    def _run_single_symbol(
        self,
        instrument_key: str,
        trading_symbol: str,
        train_start: datetime,
        train_end: datetime,
        test_start: datetime,
        test_end: datetime,
        initial_capital: float,
        timeframe: str,
        strategy_params: Dict,
        scan_id: str,
    ) -> SymbolResult:
        """Run train + test backtests for one symbol."""
        slug = instrument_key.split("|")[-1].replace(" ", "").lower()
        result = SymbolResult(symbol=instrument_key, trading_symbol=trading_symbol)

        # --- Train period ---
        train_run_id = f"{scan_id}_train_{slug}"
        try:
            self.runner.run(
                strategy_id="pixityAI_meta",
                symbol=instrument_key,
                start_time=train_start,
                end_time=train_end,
                initial_capital=initial_capital,
                strategy_params=dict(strategy_params),
                timeframe=timeframe,
                run_id=train_run_id,
            )
            result.train_run_id = train_run_id
            result.train_status = "COMPLETED"
            metrics = self._read_run_metrics(train_run_id)
            result.train_pnl = metrics["total_pnl"]
            result.train_trades = metrics["total_trades"]
            result.train_win_rate = metrics["win_rate"]
            result.train_max_dd = metrics["max_drawdown"]
        except Exception as e:
            result.train_status = "FAILED"
            result.error = f"Train: {str(e)[:200]}"
            logger.warning(f"Train failed for {trading_symbol}: {e}")
            return result  # Skip test if train fails

        # Skip test if train produced 0 events
        if result.train_trades == 0:
            result.test_status = "SKIPPED"
            result.error = "0 trades in train period"
            return result

        # --- Test period ---
        test_run_id = f"{scan_id}_test_{slug}"
        try:
            self.runner.run(
                strategy_id="pixityAI_meta",
                symbol=instrument_key,
                start_time=test_start,
                end_time=test_end,
                initial_capital=initial_capital,
                strategy_params=dict(strategy_params),
                timeframe=timeframe,
                run_id=test_run_id,
            )
            result.test_run_id = test_run_id
            result.test_status = "COMPLETED"
            metrics = self._read_run_metrics(test_run_id)
            result.test_pnl = metrics["total_pnl"]
            result.test_trades = metrics["total_trades"]
            result.test_win_rate = metrics["win_rate"]
            result.test_max_dd = metrics["max_drawdown"]
        except Exception as e:
            result.test_status = "FAILED"
            result.error = f"Test: {str(e)[:200]}"
            logger.warning(f"Test failed for {trading_symbol}: {e}")

        return result

    def _read_run_metrics(self, run_id: str) -> Dict[str, Any]:
        """Read metrics from backtest_index for a completed run."""
        try:
            with self.db.backtest_index_reader() as conn:
                row = conn.execute(
                    "SELECT total_trades, win_rate, total_pnl, max_drawdown, status "
                    "FROM backtest_runs WHERE run_id = ?",
                    [run_id],
                ).fetchone()
                if row:
                    return {
                        "total_trades": row[0] or 0,
                        "win_rate": row[1] or 0.0,
                        "total_pnl": row[2] or 0.0,
                        "max_drawdown": row[3] or 0.0,
                        "status": row[4] or "UNKNOWN",
                    }
        except Exception as e:
            logger.error(f"Failed to read metrics for {run_id}: {e}")

        return {"total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0, "max_drawdown": 0.0, "status": "UNKNOWN"}

    def _check_profitability(self, result: SymbolResult, criteria: Dict) -> bool:
        """Check if a symbol meets all profitability criteria."""
        if result.train_status != "COMPLETED" or result.test_status != "COMPLETED":
            return False

        checks = [
            result.test_pnl > criteria.get("min_test_pnl", 0.0),
            result.test_win_rate > criteria.get("min_test_win_rate", 50.0),
            result.train_pnl > criteria.get("min_train_pnl", 0.0),
            result.train_max_dd < criteria.get("max_drawdown", 20.0),
            result.test_max_dd < criteria.get("max_drawdown", 20.0),
            result.test_trades >= criteria.get("min_test_trades", 20),
        ]
        return all(checks)

    def _rank_results(self, scan: ScanResults) -> None:
        """Rank symbols by test-period PnL (profitable first, then by PnL descending)."""
        profitable = [r for r in scan.symbol_results if r.is_profitable]
        profitable.sort(key=lambda r: r.test_pnl, reverse=True)
        for i, r in enumerate(profitable):
            r.rank = i + 1

    def compute_correlation_matrix(
        self, scan: ScanResults, timeframe: str = "15m"
    ) -> Optional[pd.DataFrame]:
        """Compute pairwise return correlation for profitable symbols.

        Loads daily PnL series from each run's trade DB and computes
        Pearson correlation.
        """
        profitable = [r for r in scan.symbol_results if r.is_profitable and r.test_run_id]
        if len(profitable) < 2:
            return None

        daily_returns = {}
        for result in profitable:
            try:
                with self.db.backtest_reader(result.test_run_id) as conn:
                    df = conn.execute(
                        "SELECT exit_ts, pnl FROM trades ORDER BY exit_ts"
                    ).df()
                    if df.empty:
                        continue
                    df["date"] = pd.to_datetime(df["exit_ts"]).dt.date
                    daily = df.groupby("date")["pnl"].sum()
                    daily_returns[result.trading_symbol] = daily
            except Exception as e:
                logger.warning(f"Could not load trades for {result.trading_symbol}: {e}")

        if len(daily_returns) < 2:
            return None

        returns_df = pd.DataFrame(daily_returns).fillna(0)
        corr = returns_df.corr()
        return corr
