"""Rolling 30-day FTMO challenge simulation."""

from dataclasses import dataclass, asdict
from typing import Optional
import uuid

from ftmo.config import ACCOUNT_SIZE, CHALLENGE_DAYS
from ftmo.risk import RiskEngine, AccountState
from ftmo.engine import TradeRecord


@dataclass
class SimulationResult:
    sim_id: str
    start_date: str
    end_date: str
    starting_equity: float
    ending_equity: float
    total_pnl: float
    total_trades: int
    win_rate: float
    avg_r: float
    expectancy: float
    max_drawdown_dollar: float
    max_drawdown_pct: float
    worst_losing_streak: int
    days_to_target: Optional[int]
    passed: bool
    breached_daily_limit: bool
    breached_overall_limit: bool

    def to_dict(self) -> dict:
        return asdict(self)


class FTMOSimulator:
    def __init__(self, trades: list[TradeRecord], all_dates: list[str] = None):
        self.trades = trades
        self.risk = RiskEngine()
        self.all_dates = all_dates  # All trading days (including no-trade days)

    def run_rolling(
        self,
        window_days: int = CHALLENGE_DAYS,
        step_days: int = 1,
    ) -> list[SimulationResult]:
        """Generate rolling challenge simulations.

        Each window starts fresh at $50K and replays trades through
        the risk engine. Returns one SimulationResult per window.
        """
        # Build date -> trades mapping
        date_trades: dict[str, list[TradeRecord]] = {}
        for t in self.trades:
            date_trades.setdefault(t.session_date, []).append(t)

        # Use all calendar trading dates (not just dates with trades)
        if self.all_dates:
            trading_dates = sorted(self.all_dates)
        else:
            trading_dates = sorted(set(t.session_date for t in self.trades))

        results = []

        for i in range(0, len(trading_dates) - window_days + 1, step_days):
            window_dates = trading_dates[i: i + window_days]
            result = self._simulate_window(window_dates, date_trades)
            results.append(result)

        return results

    def _simulate_window(
        self,
        dates: list[str],
        date_trades: dict[str, list[TradeRecord]],
    ) -> SimulationResult:
        state = AccountState.fresh(ACCOUNT_SIZE)
        peak_equity = ACCOUNT_SIZE

        all_r = []
        consec_losses = 0
        worst_streak = 0
        breached_daily = False
        breached_overall = False
        passed = False
        days_to_target = None
        total_trades = 0

        for day_idx, date in enumerate(dates):
            day_trades = date_trades.get(date, [])

            for trade in day_trades:
                # Risk gate
                allowed, reason, status = self.risk.check_pre_trade(
                    state, trade.risk_amount, trade.timestamp_entry
                )
                if not allowed:
                    if "overall" in reason.lower():
                        breached_overall = True
                    if "daily" in reason.lower():
                        breached_daily = True
                    break

                # Apply trade PnL (using the pre-computed pnl_dollar from backtest)
                # Re-scale PnL based on fresh risk sizing for this simulation
                risk_dollar = self.risk.calculate_risk_per_trade(state)
                scaled_pnl = trade.pnl_r * risk_dollar  # R-multiple × risk budget

                state = self.risk.update_post_trade(state, scaled_pnl)
                total_trades += 1
                all_r.append(trade.pnl_r)

                # Track streaks
                if trade.pnl_r < 0:
                    consec_losses += 1
                    worst_streak = max(worst_streak, consec_losses)
                else:
                    consec_losses = 0

                # Track peak
                peak_equity = max(peak_equity, state.equity)

                # Check pass
                if self.risk.check_ftmo_pass(state) and not passed:
                    passed = True
                    days_to_target = day_idx + 1

            # Check breach
            breached, reason = self.risk.check_ftmo_breach(state)
            if breached:
                breached_daily = breached_daily or "daily" in reason
                breached_overall = breached_overall or "overall" in reason
                break

            state = self.risk.new_day(state)

        # Compute stats
        wins = sum(1 for r in all_r if r > 0)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        avg_r = sum(all_r) / len(all_r) if all_r else 0

        avg_win = sum(r for r in all_r if r > 0) / max(wins, 1)
        loss_count = sum(1 for r in all_r if r <= 0)
        avg_loss = abs(sum(r for r in all_r if r <= 0)) / max(loss_count, 1)
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss) if all_r else 0

        max_dd_dollar = peak_equity - min(state.equity, peak_equity)
        max_dd_pct = (max_dd_dollar / peak_equity * 100) if peak_equity > 0 else 0

        return SimulationResult(
            sim_id=str(uuid.uuid4())[:8],
            start_date=dates[0],
            end_date=dates[-1],
            starting_equity=ACCOUNT_SIZE,
            ending_equity=round(state.equity, 2),
            total_pnl=round(state.equity - ACCOUNT_SIZE, 2),
            total_trades=total_trades,
            win_rate=round(win_rate, 1),
            avg_r=round(avg_r, 2),
            expectancy=round(expectancy, 3),
            max_drawdown_dollar=round(max_dd_dollar, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            worst_losing_streak=worst_streak,
            days_to_target=days_to_target,
            passed=passed,
            breached_daily_limit=breached_daily,
            breached_overall_limit=breached_overall,
        )

    @staticmethod
    def get_aggregate_stats(results: list[SimulationResult]) -> dict:
        if not results:
            return {}

        passed = [r for r in results if r.passed]
        pass_rate = len(passed) / len(results) * 100

        days_list = [r.days_to_target for r in passed if r.days_to_target is not None]
        avg_days = sum(days_list) / len(days_list) if days_list else None

        dd_list = [r.max_drawdown_pct for r in results]
        avg_dd = sum(dd_list) / len(dd_list)
        worst_dd = max(dd_list)

        streaks = [r.worst_losing_streak for r in results]
        worst_streak = max(streaks) if streaks else 0

        wr_list = [r.win_rate for r in results]
        median_wr = sorted(wr_list)[len(wr_list) // 2] if wr_list else 0

        exp_list = [r.expectancy for r in results]
        avg_exp = sum(exp_list) / len(exp_list) if exp_list else 0

        breach_daily = sum(1 for r in results if r.breached_daily_limit)
        breach_overall = sum(1 for r in results if r.breached_overall_limit)

        return {
            "total_simulations": len(results),
            "pass_rate_pct": round(pass_rate, 1),
            "avg_days_to_pass": round(avg_days, 1) if avg_days else None,
            "avg_max_drawdown_pct": round(avg_dd, 2),
            "worst_drawdown_pct": round(worst_dd, 2),
            "worst_losing_streak": worst_streak,
            "median_win_rate": round(median_wr, 1),
            "avg_expectancy": round(avg_exp, 3),
            "daily_breach_count": breach_daily,
            "overall_breach_count": breach_overall,
        }
