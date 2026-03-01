"""Weekly circuit breaker — halts trading when weekly drawdown exceeds threshold."""
from datetime import date


class WeeklyCircuitBreaker:
    """
    Halts all trading when weekly drawdown exceeds threshold.
    Resets at start of each new trading week (Monday).
    """

    def __init__(self, max_weekly_dd_pct: float = 0.03):
        self.max_weekly_dd_pct = max_weekly_dd_pct
        self._week_start_equity: float = 0.0
        self._current_week: int = -1
        self._halted: bool = False

    def update(self, current_equity: float, current_date: date) -> bool:
        """Update with current equity. Returns True if halted."""
        week_num = current_date.isocalendar()[1]
        if self._current_week != week_num:
            self._current_week = week_num
            self._week_start_equity = current_equity
            self._halted = False

        if self._week_start_equity > 0:
            dd = (self._week_start_equity - current_equity) / self._week_start_equity
            if dd >= self.max_weekly_dd_pct:
                self._halted = True

        return self._halted

    def is_halted(self) -> bool:
        return self._halted
