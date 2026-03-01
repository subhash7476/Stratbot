"""Post-run analytics: trade metrics, equity curves, R-distribution."""

from ftmo.engine import TradeRecord
from ftmo.config import ACCOUNT_SIZE


def compute_trade_analytics(trades: list[TradeRecord]) -> dict:
    if not trades:
        return {"total_trades": 0}

    r_values = [t.pnl_r for t in trades]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]

    win_rate = len(wins) / len(r_values) * 100 if r_values else 0
    avg_r = sum(r_values) / len(r_values) if r_values else 0
    avg_winner = sum(wins) / len(wins) if wins else 0
    avg_loser = abs(sum(losses) / len(losses)) if losses else 0

    expectancy = (win_rate / 100 * avg_winner) - ((1 - win_rate / 100) * avg_loser)

    gross_profit = sum(t.pnl_dollar for t in trades if t.pnl_dollar > 0)
    gross_loss = abs(sum(t.pnl_dollar for t in trades if t.pnl_dollar <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Streaks
    max_win_streak = _max_streak(r_values, positive=True)
    max_loss_streak = _max_streak(r_values, positive=False)

    # Total PnL
    total_pnl = sum(t.pnl_dollar for t in trades)

    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "avg_r": round(avg_r, 2),
        "avg_winner_r": round(avg_winner, 2),
        "avg_loser_r": round(avg_loser, 2),
        "expectancy": round(expectancy, 3),
        "profit_factor": round(profit_factor, 2),
        "total_pnl_dollar": round(total_pnl, 2),
        "max_winning_streak": max_win_streak,
        "max_losing_streak": max_loss_streak,
        "r_distribution": [round(r, 2) for r in r_values],
        "exit_reasons": exit_reasons,
    }


def compute_equity_curve(
    trades: list[TradeRecord],
    starting_equity: float = ACCOUNT_SIZE,
) -> list[dict]:
    """Build equity curve with drawdown tracking."""
    curve = [{"timestamp": "start", "equity": starting_equity, "drawdown_pct": 0.0}]
    equity = starting_equity
    peak = starting_equity

    for t in trades:
        equity += t.pnl_dollar
        peak = max(peak, equity)
        dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0

        curve.append({
            "timestamp": str(t.timestamp_exit),
            "equity": round(equity, 2),
            "drawdown_pct": round(dd_pct, 2),
        })

    return curve


def compute_daily_summary(daily_stats: list[dict]) -> dict:
    """Aggregate daily stats for the overall backtest run."""
    if not daily_stats:
        return {}

    total_days = len(daily_stats)
    trading_days = sum(1 for d in daily_stats if d.get("trades_taken", 0) > 0)
    total_pnl = sum(d.get("daily_pnl", 0) for d in daily_stats)
    max_daily_loss = min(d.get("daily_pnl", 0) for d in daily_stats)
    max_daily_gain = max(d.get("daily_pnl", 0) for d in daily_stats)
    max_dd = max(d.get("overall_drawdown", 0) for d in daily_stats)

    return {
        "total_sessions": total_days,
        "trading_days": trading_days,
        "idle_days": total_days - trading_days,
        "total_pnl_dollar": round(total_pnl, 2),
        "max_daily_loss": round(max_daily_loss, 2),
        "max_daily_gain": round(max_daily_gain, 2),
        "max_overall_drawdown": round(max_dd, 2),
    }


def _max_streak(r_values: list[float], positive: bool) -> int:
    max_s = 0
    current = 0
    for r in r_values:
        if (positive and r > 0) or (not positive and r <= 0):
            current += 1
            max_s = max(max_s, current)
        else:
            current = 0
    return max_s
