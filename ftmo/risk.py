"""Dual-layer risk engine: FTMO hard limits + internal overlay."""

from dataclasses import dataclass, replace
from datetime import datetime

from ftmo.config import (
    ACCOUNT_SIZE,
    PROFIT_TARGET,
    DAILY_MAX_LOSS,
    MAX_OVERALL_LOSS,
    RISK_PER_TRADE_PCT,
    DAILY_STOP_PCT,
    MAX_TRADES_PER_DAY,
    MAX_CONSECUTIVE_LOSSES,
    REDUCED_RISK_PCT,
    REDUCED_RISK_THRESHOLD,
    POINT_VALUE,
    NY_END,
    RiskStatus,
)


@dataclass
class AccountState:
    equity: float
    starting_balance: float
    daily_starting_equity: float
    max_equity: float
    trades_today: int
    consecutive_losses: int
    daily_pnl: float

    @classmethod
    def fresh(cls, balance: float = ACCOUNT_SIZE) -> "AccountState":
        return cls(
            equity=balance,
            starting_balance=balance,
            daily_starting_equity=balance,
            max_equity=balance,
            trades_today=0,
            consecutive_losses=0,
            daily_pnl=0.0,
        )


class RiskEngine:
    def check_pre_trade(
        self, state: AccountState, risk_amount: float, current_time: datetime = None,
    ) -> tuple[bool, str, RiskStatus]:
        """Pre-trade gate. Returns (allowed, reason, status)."""
        # Layer 1: FTMO hard limits
        overall_loss = state.starting_balance - state.equity
        if overall_loss >= MAX_OVERALL_LOSS:
            return False, "FTMO overall loss limit breached", RiskStatus.STOP

        daily_loss = -state.daily_pnl
        if daily_loss >= DAILY_MAX_LOSS:
            return False, "FTMO daily loss limit breached", RiskStatus.STOP

        # Layer 2: Internal overlay
        internal_daily_limit = state.starting_balance * DAILY_STOP_PCT
        if daily_loss >= internal_daily_limit:
            return False, f"Internal daily stop (-${internal_daily_limit:.0f})", RiskStatus.STOP

        if state.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False, f"Stopped: {state.consecutive_losses} consecutive losses", RiskStatus.STOP

        if state.trades_today >= MAX_TRADES_PER_DAY:
            return False, f"Max {MAX_TRADES_PER_DAY} trades/day reached", RiskStatus.STOP

        if current_time is not None and current_time.time() >= NY_END:
            return False, "Past 8:00 PM IST cutoff", RiskStatus.STOP

        # Caution zone: approaching limits
        if daily_loss >= internal_daily_limit * 0.7:
            return True, "Approaching daily limit", RiskStatus.CAUTION

        if overall_loss >= MAX_OVERALL_LOSS * 0.7:
            return True, "Approaching overall loss limit", RiskStatus.CAUTION

        return True, "Clear", RiskStatus.GREEN

    def calculate_risk_per_trade(self, state: AccountState) -> float:
        """Return dollar risk per trade based on current equity."""
        equity_gain_pct = (state.equity - state.starting_balance) / state.starting_balance
        risk_pct = REDUCED_RISK_PCT if equity_gain_pct >= REDUCED_RISK_THRESHOLD else RISK_PER_TRADE_PCT
        return state.equity * risk_pct

    def calculate_lot_size(self, state: AccountState, stop_distance_points: float) -> float:
        """Return lot size (fractional) based on risk budget and stop distance."""
        if stop_distance_points <= 0:
            return 0.0
        risk_dollar = self.calculate_risk_per_trade(state)
        return risk_dollar / (stop_distance_points * POINT_VALUE)

    def update_post_trade(self, state: AccountState, pnl_dollar: float) -> AccountState:
        """Update state after a trade closes."""
        new_equity = state.equity + pnl_dollar
        new_daily_pnl = state.daily_pnl + pnl_dollar
        is_loss = pnl_dollar < 0
        new_consec = state.consecutive_losses + 1 if is_loss else 0
        new_max = max(state.max_equity, new_equity)

        return replace(
            state,
            equity=new_equity,
            daily_pnl=new_daily_pnl,
            max_equity=new_max,
            trades_today=state.trades_today + 1,
            consecutive_losses=new_consec,
        )

    def new_day(self, state: AccountState) -> AccountState:
        """Reset daily counters. Consecutive losses carry across days."""
        return replace(
            state,
            daily_starting_equity=state.equity,
            trades_today=0,
            daily_pnl=0.0,
        )

    def check_ftmo_pass(self, state: AccountState) -> bool:
        return state.equity >= state.starting_balance + PROFIT_TARGET

    def check_ftmo_breach(self, state: AccountState) -> tuple[bool, str]:
        overall_loss = state.starting_balance - state.equity
        if overall_loss >= MAX_OVERALL_LOSS:
            return True, "overall"

        daily_loss = -state.daily_pnl
        if daily_loss >= DAILY_MAX_LOSS:
            return True, "daily"

        return False, ""

    def get_status(self, state: AccountState) -> RiskStatus:
        overall_loss = state.starting_balance - state.equity
        daily_loss = -state.daily_pnl
        internal_daily = state.starting_balance * DAILY_STOP_PCT

        if (overall_loss >= MAX_OVERALL_LOSS or daily_loss >= DAILY_MAX_LOSS
                or daily_loss >= internal_daily
                or state.consecutive_losses >= MAX_CONSECUTIVE_LOSSES):
            return RiskStatus.STOP

        if (daily_loss >= internal_daily * 0.7
                or overall_loss >= MAX_OVERALL_LOSS * 0.7):
            return RiskStatus.CAUTION

        return RiskStatus.GREEN
