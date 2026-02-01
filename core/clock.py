"""
Clock - Single Source of Truth for Time
---------------------------------------
Abstracts time to support both live trading and historical replay.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import pytz

class Clock(ABC):
    """
    Abstract base class for all clocks.
    """
    
    @abstractmethod
    def now(self) -> datetime:
        """Returns the current 'system' time."""
        pass

    def sleep(self, seconds: float):
        """Simulates or performs an actual sleep."""
        pass


class RealTimeClock(Clock):
    """
    Clock implementation for live trading.
    """
    
    def __init__(self, timezone: str = 'Asia/Kolkata'):
        self.tz = pytz.timezone(timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def sleep(self, seconds: float):
        import time
        time.sleep(seconds)


class ReplayClock(Clock):
    """
    Clock implementation for historical backtesting.
    Time only advances when manually stepped or when data is consumed.
    """
    
    def __init__(self, start_time: datetime):
        self._current_time = start_time

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, dt: datetime):
        """Manually advance the clock."""
        self._current_time = dt

    def advance(self, delta: timedelta):
        """Advance the clock by a duration."""
        self._current_time += delta
