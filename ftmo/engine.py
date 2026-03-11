"""Backtest engine: session-by-session replay with bar-by-bar trade management."""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional
import uuid
import logging
import pandas as pd

from ftmo.config import (
    NY_END, NY2_START, NY2_END, PRE_NY2_START, PRE_NY2_END,
    POINT_VALUE, RiskStatus, ACCOUNT_SIZE,
)
from ftmo.indicators import enrich_with_indicators
from ftmo.session import compute_pre_ny_ranges, get_all_ny_bars, get_trading_dates
from ftmo.detector import scan_session, TradeSetup
from ftmo.risk import RiskEngine, AccountState

logger = logging.getLogger(__name__)


def _precompute_session2(df_m5: pd.DataFrame) -> tuple[dict, dict]:
    """Pre-compute Session 2: London range (13:30-19:00 IST) as pre-session reference,
    NY trading window (19:00-23:00 IST). Returns (pre_ranges_dict, ny_bars_dict)."""
    from datetime import time as dtime
    from ftmo.config import PRE_NY2_START, PRE_NY2_END, NY2_START, NY2_END

    df = df_m5.copy()
    t = df["timestamp"].dt.time
    df["s2_pre"] = (t >= PRE_NY2_START) & (t < PRE_NY2_END)
    df["s2_ny"] = (t >= NY2_START) & (t < NY2_END)
    df["session_date"] = df["timestamp"].dt.strftime("%Y-%m-%d")

    pre_ranges = {}
    for date, group in df[df["s2_pre"]].groupby("session_date"):
        if len(group) < 2:
            continue
        pre_ranges[date] = {"high": group["high"].max(), "low": group["low"].min()}

    ny_bars = {
        date: group.reset_index(drop=True)
        for date, group in df[df["s2_ny"]].groupby("session_date")
    }
    return pre_ranges, ny_bars


@dataclass
class TradeRecord:
    trade_id: str
    timestamp_entry: datetime
    timestamp_exit: datetime
    direction: str
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    risk_amount: float
    pnl_dollar: float
    pnl_r: float
    exit_reason: str
    sweep_direction: str
    sweep_price: float
    pre_ny_high: float
    pre_ny_low: float
    m15_atr: float
    m5_atr: float
    session_date: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp_entry"] = str(d["timestamp_entry"])
        d["timestamp_exit"] = str(d["timestamp_exit"])
        return d


@dataclass
class DailyStatRecord:
    session_date: str
    starting_equity: float
    ending_equity: float
    daily_pnl: float
    daily_pnl_pct: float
    trades_taken: int
    wins: int
    losses: int
    max_equity: float
    daily_drawdown: float
    overall_drawdown: float
    risk_status: str
    consecutive_losses: int


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    daily_stats: list[DailyStatRecord]
    equity_curve: list[tuple[str, float]]  # (timestamp_str, equity)
    final_equity: float
    total_pnl: float


class FTMOBacktestEngine:
    def __init__(self, df_m5: pd.DataFrame):
        self.df_m5_raw = df_m5
        self.risk = RiskEngine()

    def run(
        self,
        start_date: str = None,
        end_date: str = None,
        starting_balance: float = None,
    ) -> BacktestResult:
        # Enrich with indicators
        df_m5, df_m15 = enrich_with_indicators(self.df_m5_raw)

        # Pre-compute all session data in one vectorised pass
        pre_ny_ranges = compute_pre_ny_ranges(df_m5)
        all_ny_bars = get_all_ny_bars(df_m5)
        trading_dates = get_trading_dates(df_m5)

        # Session 2: London range → NY sweep (pre-group once)
        _s2_pre, _s2_ny = _precompute_session2(df_m5)

        if start_date:
            trading_dates = [d for d in trading_dates if d >= start_date]
        if end_date:
            trading_dates = [d for d in trading_dates if d <= end_date]

        _balance = starting_balance if starting_balance is not None else ACCOUNT_SIZE
        state = AccountState.fresh(_balance)
        all_trades: list[TradeRecord] = []
        all_daily: list[DailyStatRecord] = []
        equity_curve: list[tuple[str, float]] = []

        equity_curve.append((trading_dates[0] if trading_dates else "start", state.equity))

        for date in trading_dates:
            if date not in pre_ny_ranges:
                continue

            pre_ny = pre_ny_ranges[date]
            df_ny = all_ny_bars.get(date, pd.DataFrame())
            if len(df_ny) == 0:
                continue

            # Get M15 ATR at session start (first NY bar's m15_atr)
            m15_atr = df_ny.iloc[0]["m15_atr"] if "m15_atr" in df_ny.columns else 0
            if pd.isna(m15_atr) or m15_atr <= 0:
                continue

            day_start_equity = state.equity
            day_wins = 0
            day_losses = 0

            # Collect setups from both sessions, sorted by entry time
            setups = scan_session(df_ny, pre_ny.high, pre_ny.low, m15_atr)

            # Session 2: London range → NY open sweep
            s2_pre = _s2_pre.get(date)
            s2_ny = _s2_ny.get(date, pd.DataFrame())
            if s2_pre is not None and len(s2_ny) > 0:
                s2_m15_atr = s2_ny.iloc[0]["m15_atr"] if "m15_atr" in s2_ny.columns else m15_atr
                if not pd.isna(s2_m15_atr) and s2_m15_atr > 0:
                    from ftmo.config import NY2_END as _NY2_END
                    s2_setups = scan_session(s2_ny, s2_pre["high"], s2_pre["low"], s2_m15_atr, cutoff=_NY2_END)
                    setups = sorted(setups + s2_setups, key=lambda s: s.timestamp)

            for setup in setups:
                # Risk gate
                risk_dollar = self.risk.calculate_risk_per_trade(state)
                allowed, reason, status = self.risk.check_pre_trade(
                    state, risk_dollar, setup.timestamp
                )
                if not allowed:
                    logger.debug(f"  {date} trade blocked: {reason}")
                    break

                # Determine which bar universe to use for trade management
                df_trade_bars = s2_ny if (
                    s2_pre is not None and len(s2_ny) > 0
                    and setup.timestamp >= s2_ny.iloc[0]["timestamp"]
                ) else df_ny

                # Calculate lot size and dollar risk
                lot_size = self.risk.calculate_lot_size(state, setup.risk_points)

                # Determine cutoff time for this setup's session
                from ftmo.config import NY2_END
                is_s2 = (
                    s2_pre is not None and len(s2_ny) > 0
                    and setup.timestamp >= s2_ny.iloc[0]["timestamp"]
                )
                cutoff = NY2_END if is_s2 else NY_END

                # Manage trade bar-by-bar
                trade = self._manage_trade(setup, df_trade_bars, date, pre_ny, m15_atr, lot_size, cutoff)
                if trade is None:
                    continue

                # Update risk state
                state = self.risk.update_post_trade(state, trade.pnl_dollar)
                all_trades.append(trade)
                equity_curve.append((str(trade.timestamp_exit), state.equity))

                if trade.pnl_dollar > 0:
                    day_wins += 1
                else:
                    day_losses += 1

                # Check FTMO breach mid-day
                breached, reason = self.risk.check_ftmo_breach(state)
                if breached:
                    break

            # Daily stats
            daily_dd = day_start_equity - min(
                state.equity,
                day_start_equity,  # Intraday tracking simplified
            )
            overall_dd = state.max_equity - state.equity

            daily_stat = DailyStatRecord(
                session_date=date,
                starting_equity=day_start_equity,
                ending_equity=state.equity,
                daily_pnl=state.daily_pnl,
                daily_pnl_pct=(state.daily_pnl / day_start_equity * 100) if day_start_equity > 0 else 0,
                trades_taken=state.trades_today,
                wins=day_wins,
                losses=day_losses,
                max_equity=state.max_equity,
                daily_drawdown=daily_dd,
                overall_drawdown=overall_dd,
                risk_status=self.risk.get_status(state).value,
                consecutive_losses=state.consecutive_losses,
            )
            all_daily.append(daily_stat)

            # New day
            state = self.risk.new_day(state)

        return BacktestResult(
            trades=all_trades,
            daily_stats=all_daily,
            equity_curve=equity_curve,
            final_equity=state.equity,
            total_pnl=state.equity - _balance,
        )

    def _manage_trade(
        self,
        setup: TradeSetup,
        df_ny: pd.DataFrame,
        session_date: str,
        pre_ny,
        m15_atr: float,
        lot_size: float,
        session_cutoff=None,
    ) -> Optional[TradeRecord]:
        """Bar-by-bar trade management after entry."""
        # Find the entry bar index in df_ny
        entry_mask = df_ny["timestamp"] >= setup.timestamp
        entry_bars = df_ny[entry_mask]
        if len(entry_bars) == 0:
            return None

        exit_price = None
        exit_time = None
        exit_reason = None

        for i in range(len(entry_bars)):
            bar = entry_bars.iloc[i]

            # Skip the entry bar itself for exit checks (entered on this bar)
            if i == 0:
                continue

            if setup.direction == "SHORT":
                # SL: price goes above stop
                if bar["high"] >= setup.stop_loss:
                    exit_price = setup.stop_loss
                    exit_time = bar["timestamp"]
                    exit_reason = "SL"
                    break
                # TP: price goes below target
                if bar["low"] <= setup.take_profit:
                    exit_price = setup.take_profit
                    exit_time = bar["timestamp"]
                    exit_reason = "TP"
                    break
            else:  # LONG
                # SL: price goes below stop
                if bar["low"] <= setup.stop_loss:
                    exit_price = setup.stop_loss
                    exit_time = bar["timestamp"]
                    exit_reason = "SL"
                    break
                # TP: price goes above target
                if bar["high"] >= setup.take_profit:
                    exit_price = setup.take_profit
                    exit_time = bar["timestamp"]
                    exit_reason = "TP"
                    break

            # Time cutoff: use session-specific cutoff or global NY_END
            _cutoff = session_cutoff if session_cutoff is not None else NY_END
            if bar["timestamp"].time() >= _cutoff:
                exit_price = bar["close"]
                exit_time = bar["timestamp"]
                exit_reason = "TIME_CUTOFF"
                break

        # If still open at end of session data, close at last bar
        if exit_price is None:
            last_bar = entry_bars.iloc[-1]
            exit_price = last_bar["close"]
            exit_time = last_bar["timestamp"]
            exit_reason = "TIME_CUTOFF"

        # Calculate PnL
        if setup.direction == "SHORT":
            pnl_points = setup.entry_price - exit_price
        else:
            pnl_points = exit_price - setup.entry_price

        pnl_dollar = pnl_points * lot_size * POINT_VALUE
        pnl_r = pnl_points / setup.risk_points if setup.risk_points > 0 else 0

        # Get M5 ATR at entry
        entry_bar = entry_bars.iloc[0]
        m5_atr = entry_bar["atr"] if "atr" in entry_bar.index else 0

        return TradeRecord(
            trade_id=str(uuid.uuid4())[:8],
            timestamp_entry=setup.timestamp,
            timestamp_exit=exit_time,
            direction=setup.direction,
            entry_price=setup.entry_price,
            exit_price=exit_price,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            risk_amount=lot_size * setup.risk_points * POINT_VALUE,
            pnl_dollar=round(pnl_dollar, 2),
            pnl_r=round(pnl_r, 2),
            exit_reason=exit_reason,
            sweep_direction=setup.sweep.direction,
            sweep_price=setup.sweep.sweep_price,
            pre_ny_high=pre_ny.high,
            pre_ny_low=pre_ny.low,
            m15_atr=round(m15_atr, 2),
            m5_atr=round(m5_atr, 2) if not pd.isna(m5_atr) else 0,
            session_date=session_date,
        )
