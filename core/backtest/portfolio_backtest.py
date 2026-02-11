"""
Portfolio Backtest Runner
-------------------------
Runs per-symbol backtests via the real BacktestRunner (correct bar-by-bar
SL/TP/time_stop logic), then applies portfolio-level constraints
(max concurrent positions, correlation limits) as a post-filter on the
combined trade stream.

Architecture:
  1. Run BacktestRunner.run() per symbol -> per-symbol DuckDB with trades
  2. Load paired trades from each run
  3. Merge all trades into a single time-sorted stream
  4. Walk through entries chronologically, applying portfolio constraints
  5. Accepted trades keep their pre-computed exits (from TradingRunner)
  6. Calculate portfolio-level metrics from accepted trades
  7. Save combined results
"""
import uuid
import json
import logging
import duckdb
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass

from core.database.manager import DatabaseManager
from core.database import schema
from core.backtest.runner import BacktestRunner
from core.portfolio.allocator import PortfolioAllocator

logger = logging.getLogger(__name__)


@dataclass
class PairedTrade:
    """A completed entry+exit trade loaded from a per-symbol backtest."""
    symbol: str
    trading_symbol: str
    entry_ts: datetime
    exit_ts: datetime
    direction: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    fees: float
    per_symbol_run_id: str


class PortfolioBacktestRunner:
    """Run portfolio-level backtest across multiple symbols.

    Delegates per-symbol execution to the real BacktestRunner (which uses
    TradingRunner with correct bar-by-bar SL/TP/time_stop exit logic),
    then applies portfolio-level position constraints.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.runner = BacktestRunner(db_manager)

    def run(
        self,
        symbols: List[Dict[str, str]],
        start_time: datetime,
        end_time: datetime,
        total_capital: float = 500000.0,
        timeframe: str = "15m",
        allocation_method: str = "equal_weight",
        max_concurrent_positions: int = 5,
        max_correlation: float = 0.7,
        symbol_ranks: Optional[Dict[str, int]] = None,
        correlation_matrix: Optional[pd.DataFrame] = None,
        run_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> str:
        """
        Run a portfolio backtest.

        Steps:
          1. Allocate capital across symbols
          2. Run BacktestRunner.run() per symbol (correct SL/TP/time_stop)
          3. Load paired trades from each per-symbol run
          4. Walk through combined entries chronologically with portfolio constraints
          5. Save portfolio-level results
          6. Return run_id
        """
        if run_id is None:
            run_id = f"portfolio_{uuid.uuid4().hex[:8]}"

        # ── Step 1: Capital allocation ──────────────────────────────
        symbol_keys = [s["instrument_key"] for s in symbols]
        allocator = PortfolioAllocator(total_capital, max_concurrent_positions)

        if allocation_method == "rank_weighted":
            if symbol_ranks is None:
                raise ValueError("symbol_ranks required for rank_weighted allocation")
            allocations = allocator.rank_weighted(symbol_ranks)
        elif allocation_method in ("inverse_volatility", "risk_parity"):
            # Volatilities will be computed after per-symbol runs; use equal_weight initially
            # (per-symbol capital doesn't affect trade sizing since PixityAI uses fixed Rs 500 risk)
            allocations = allocator.equal_weight(symbol_keys)
        else:
            allocations = allocator.equal_weight(symbol_keys)

        if progress_callback:
            progress_callback(f"Allocated capital: {allocation_method} across {len(allocations)} symbols")

        # ── Step 2: Per-symbol backtests via real BacktestRunner ────
        per_symbol_run_ids = {}
        per_symbol_errors = {}

        for i, sym_info in enumerate(symbols):
            symbol = sym_info["instrument_key"]
            trading_symbol = sym_info.get("trading_symbol", symbol)
            symbol_capital = allocations.get(symbol, total_capital / len(symbols))

            if progress_callback:
                progress_callback(f"Running backtest {i+1}/{len(symbols)}: {trading_symbol}")

            try:
                sym_run_id = f"{run_id}__{symbol.split('|')[-1][:12]}"
                self.runner.run(
                    strategy_id="pixityAI_meta",
                    symbol=symbol,
                    start_time=start_time,
                    end_time=end_time,
                    initial_capital=symbol_capital,
                    strategy_params={"skip_meta_model": True, "use_signal_quality_filter": False},
                    timeframe=timeframe,
                    run_id=sym_run_id,
                )
                per_symbol_run_ids[symbol] = (sym_run_id, trading_symbol)
                logger.info(f"Symbol {trading_symbol}: backtest completed -> {sym_run_id}")
            except Exception as e:
                logger.error(f"Symbol {trading_symbol}: backtest failed: {e}")
                per_symbol_errors[symbol] = str(e)

        if not per_symbol_run_ids:
            raise RuntimeError(f"All {len(symbols)} symbol backtests failed: {per_symbol_errors}")

        if progress_callback:
            progress_callback(f"Completed {len(per_symbol_run_ids)}/{len(symbols)} symbol backtests")

        # ── Step 3: Load paired trades from each run ────────────────
        all_trades: List[PairedTrade] = []

        for symbol, (sym_run_id, trading_symbol) in per_symbol_run_ids.items():
            try:
                with self.db.backtest_reader(sym_run_id) as conn:
                    rows = conn.execute("""
                        SELECT symbol, entry_ts, exit_ts, direction,
                               entry_price, exit_price, qty, pnl, fees
                        FROM trades ORDER BY entry_ts
                    """).fetchall()

                    for row in rows:
                        all_trades.append(PairedTrade(
                            symbol=symbol,
                            trading_symbol=trading_symbol,
                            entry_ts=pd.Timestamp(row[1]),
                            exit_ts=pd.Timestamp(row[2]),
                            direction=row[3],
                            entry_price=float(row[4]),
                            exit_price=float(row[5]),
                            quantity=int(row[6]),
                            pnl=float(row[7]),
                            fees=float(row[8]),
                            per_symbol_run_id=sym_run_id,
                        ))
            except Exception as e:
                logger.error(f"Failed to load trades for {symbol} ({sym_run_id}): {e}")

        logger.info(f"Loaded {len(all_trades)} total trades across {len(per_symbol_run_ids)} symbols")

        # ── Step 4: Portfolio-level simulation ──────────────────────
        # Sort by entry time
        all_trades.sort(key=lambda t: t.entry_ts)

        open_positions: Dict[str, PairedTrade] = {}   # symbol -> trade
        accepted_trades: List[PairedTrade] = []
        rejected_count = 0

        for trade in all_trades:
            # First: close any open positions whose exit_ts <= this trade's entry_ts
            symbols_to_close = [
                sym for sym, pos in open_positions.items()
                if pos.exit_ts <= trade.entry_ts
            ]
            for sym in symbols_to_close:
                del open_positions[sym]

            # Check portfolio constraints
            if trade.symbol in open_positions:
                # Symbol already has an open position at portfolio level — skip
                rejected_count += 1
                continue

            if len(open_positions) >= max_concurrent_positions:
                rejected_count += 1
                continue

            # Check correlation constraint
            if correlation_matrix is not None and open_positions:
                corr_exceeded = False
                for open_sym in open_positions:
                    try:
                        if (open_sym in correlation_matrix.index and
                                trade.symbol in correlation_matrix.columns):
                            corr = abs(correlation_matrix.loc[open_sym, trade.symbol])
                            if corr > max_correlation:
                                corr_exceeded = True
                                break
                    except (KeyError, ValueError):
                        pass
                if corr_exceeded:
                    rejected_count += 1
                    continue

            # Accept this trade
            open_positions[trade.symbol] = trade
            accepted_trades.append(trade)

        logger.info(
            f"Portfolio filter: {len(accepted_trades)} accepted, "
            f"{rejected_count} rejected (max_concurrent={max_concurrent_positions})"
        )

        # ── Step 5: Calculate portfolio metrics ─────────────────────
        metrics = self._calculate_portfolio_metrics(accepted_trades, total_capital)

        if progress_callback:
            progress_callback(
                f"Portfolio: {metrics['total_trades']} trades, "
                f"PnL Rs {metrics['total_pnl']:,.0f}, "
                f"WR {metrics['win_rate']:.1f}%, DD {metrics['max_drawdown_pct']:.1f}%"
            )

        # ── Step 6: Save combined results ───────────────────────────
        self._save_portfolio_results(
            run_id=run_id,
            accepted_trades=accepted_trades,
            metrics=metrics,
            symbols=symbols,
            start_time=start_time,
            end_time=end_time,
            total_capital=total_capital,
            allocation_method=allocation_method,
            max_concurrent_positions=max_concurrent_positions,
            max_correlation=max_correlation,
            timeframe=timeframe,
            per_symbol_run_ids=per_symbol_run_ids,
            per_symbol_errors=per_symbol_errors,
        )

        if progress_callback:
            progress_callback("Portfolio backtest completed successfully!")

        return run_id

    # ─────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────

    def _calculate_portfolio_metrics(
        self, trades: List[PairedTrade], total_capital: float
    ) -> Dict[str, Any]:
        """Calculate portfolio-level metrics from accepted trades."""
        if not trades:
            return {
                "total_pnl": 0.0, "total_pnl_net": 0.0,
                "max_drawdown_pct": 0.0, "win_rate": 0.0,
                "total_trades": 0, "avg_trade_pnl": 0.0,
                "per_symbol": {},
            }

        # Overall metrics
        pnls = [t.pnl for t in trades]
        fees = [t.fees for t in trades]
        total_pnl = sum(pnls)
        total_fees = sum(fees)
        total_pnl_net = total_pnl - total_fees
        wins = sum(1 for p in pnls if p > 0)
        win_rate = (wins / len(pnls) * 100) if pnls else 0.0

        # Max drawdown from cumulative PnL
        cum_pnl = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cum_pnl)
        drawdowns = running_max - cum_pnl
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
        max_dd_pct = (max_dd / total_capital * 100) if total_capital > 0 else 0.0

        # Per-symbol breakdown
        per_symbol: Dict[str, Dict] = {}
        for t in trades:
            key = t.trading_symbol
            if key not in per_symbol:
                per_symbol[key] = {"pnl": 0.0, "trades": 0, "wins": 0, "fees": 0.0}
            per_symbol[key]["pnl"] += t.pnl
            per_symbol[key]["fees"] += t.fees
            per_symbol[key]["trades"] += 1
            if t.pnl > 0:
                per_symbol[key]["wins"] += 1

        for stats in per_symbol.values():
            stats["win_rate"] = (stats["wins"] / stats["trades"] * 100) if stats["trades"] > 0 else 0.0

        return {
            "total_pnl": total_pnl,
            "total_pnl_net": total_pnl_net,
            "total_fees": total_fees,
            "max_drawdown_pct": max_dd_pct,
            "win_rate": win_rate,
            "total_trades": len(trades),
            "avg_trade_pnl": total_pnl / len(trades),
            "per_symbol": per_symbol,
        }

    def _save_portfolio_results(
        self,
        run_id: str,
        accepted_trades: List[PairedTrade],
        metrics: Dict[str, Any],
        symbols: List[Dict[str, str]],
        start_time: datetime,
        end_time: datetime,
        total_capital: float,
        allocation_method: str,
        max_concurrent_positions: int,
        max_correlation: float,
        timeframe: str,
        per_symbol_run_ids: Dict[str, tuple],
        per_symbol_errors: Dict[str, str],
    ):
        """Save portfolio results to DuckDB (trades) and SQLite (index)."""
        # Save trades to portfolio-specific DuckDB
        with self.db.backtest_writer(run_id) as conn:
            conn.execute(schema.BACKTEST_RUN_TRADES_SCHEMA)

            for i, t in enumerate(accepted_trades):
                conn.execute(
                    "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"pt_{i}",
                        t.symbol,
                        t.entry_ts.to_pydatetime(),
                        t.exit_ts.to_pydatetime(),
                        t.direction,
                        t.entry_price,
                        t.exit_price,
                        t.quantity,
                        t.pnl,
                        t.fees,
                        json.dumps({"trading_symbol": t.trading_symbol,
                                    "per_symbol_run_id": t.per_symbol_run_id}),
                    ),
                )

        # Update backtest index (UPSERT to handle both cases: pre-created PENDING or new)
        params_json = json.dumps({
            "type": "portfolio",
            "total_capital": total_capital,
            "allocation_method": allocation_method,
            "max_concurrent_positions": max_concurrent_positions,
            "max_correlation": max_correlation,
            "timeframe": timeframe,
            "per_symbol_run_ids": {s: rid for s, (rid, _) in per_symbol_run_ids.items()},
            "per_symbol_errors": per_symbol_errors,
            "per_symbol_metrics": metrics.get("per_symbol", {}),
        })

        with self.db.backtest_index_writer() as conn:
            conn.execute("""
                INSERT INTO backtest_runs
                    (run_id, strategy_id, symbol, start_date, end_date,
                     total_pnl, max_drawdown, sharpe_ratio, win_rate,
                     total_trades, status, created_at, params)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    total_pnl = excluded.total_pnl,
                    max_drawdown = excluded.max_drawdown,
                    win_rate = excluded.win_rate,
                    total_trades = excluded.total_trades,
                    status = 'COMPLETED',
                    params = excluded.params
            """, [
                run_id,
                f"portfolio_{allocation_method}",
                json.dumps([s.get("trading_symbol", s["instrument_key"]) for s in symbols]),
                start_time.strftime('%Y-%m-%d'),
                end_time.strftime('%Y-%m-%d'),
                metrics["total_pnl"],
                metrics["max_drawdown_pct"],
                0.0,  # sharpe — needs daily returns, not per-trade
                metrics["win_rate"],
                metrics["total_trades"],
                datetime.now().isoformat(),
                params_json,
            ])

        logger.info(f"Portfolio {run_id}: saved {metrics['total_trades']} trades, PnL={metrics['total_pnl']:.0f}")
