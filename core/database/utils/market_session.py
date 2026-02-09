"""
Market Session Utility
----------------------
Utilities for working with market trading sessions.

A session represents a single trading day with its timestamps
and provides utilities for session-based calculations.
"""

from datetime import datetime, date, time, timedelta
from typing import Optional, Tuple
import pytz


class MarketSession:
    """
    Represents a market trading session (single trading day).

    Useful for:
    - Determining which session a timestamp belongs to
    - Getting session boundaries for VWAP calculations
    - Managing session-based state

    Usage:
        from core.database.utils import MarketSession

        # Get today's session
        session = MarketSession.today()

        # Check if a timestamp is in session
        if session.contains(bar.timestamp):
            # Process bar...

        # Get session boundaries
        start, end = session.get_boundaries()
    """

    IST = pytz.timezone("Asia/Kolkata")

    # Session times (IST)
    SESSION_START = time(9, 15)  # Market open
    SESSION_END = time(15, 30)  # Market close

    def __init__(self, session_date: date):
        """
        Initialize a market session for a specific date.

        Args:
            session_date: The date of the trading session.
        """
        self.session_date = session_date

        # Pre-compute boundaries
        self._start = self.IST.localize(
            datetime.combine(session_date, self.SESSION_START)
        )
        self._end = self.IST.localize(
            datetime.combine(session_date, self.SESSION_END)
        )

    @classmethod
    def today(cls) -> "MarketSession":
        """
        Create a session for today.

        Returns:
            MarketSession for today's date.
        """
        return cls(datetime.now(cls.IST).date())

    @classmethod
    def for_timestamp(cls, ts: datetime) -> "MarketSession":
        """
        Get the session that a timestamp belongs to.

        If the timestamp is before market open, it's considered
        part of the previous session.

        Args:
            ts: The timestamp to check.

        Returns:
            MarketSession containing the timestamp.
        """
        if ts.tzinfo is None:
            ts = cls.IST.localize(ts)
        else:
            ts = ts.astimezone(cls.IST)

        ts_date = ts.date()
        ts_time = ts.time()

        # If before market open, belongs to previous session
        if ts_time < cls.SESSION_START:
            ts_date -= timedelta(days=1)

        return cls(ts_date)

    @classmethod
    def from_datetime(cls, dt: datetime) -> "MarketSession":
        """
        Create a session from a datetime.

        Args:
            dt: Datetime to create session for.

        Returns:
            MarketSession for the datetime's date.
        """
        if dt.tzinfo is None:
            dt = cls.IST.localize(dt)
        else:
            dt = dt.astimezone(cls.IST)
        return cls(dt.date())

    def get_boundaries(self) -> Tuple[datetime, datetime]:
        """
        Get the session start and end times.

        Returns:
            Tuple of (start_datetime, end_datetime) in IST.
        """
        return (self._start, self._end)

    def contains(self, ts: datetime) -> bool:
        """
        Check if a timestamp falls within this session.

        Args:
            ts: Timestamp to check.

        Returns:
            True if timestamp is within session boundaries.
        """
        if ts.tzinfo is None:
            ts = self.IST.localize(ts)
        else:
            ts = ts.astimezone(self.IST)

        return self._start <= ts < self._end

    def get_elapsed_time(self, ts: datetime) -> timedelta:
        """
        Get time elapsed since session start.

        Args:
            ts: Current timestamp.

        Returns:
            Timedelta since session start.
        """
        if ts.tzinfo is None:
            ts = self.IST.localize(ts)
        else:
            ts = ts.astimezone(self.IST)

        return ts - self._start

    def get_remaining_time(self, ts: datetime) -> timedelta:
        """
        Get time remaining until session end.

        Args:
            ts: Current timestamp.

        Returns:
            Timedelta until session end.
        """
        if ts.tzinfo is None:
            ts = self.IST.localize(ts)
        else:
            ts = ts.astimezone(self.IST)

        return self._end - ts

    def get_progress(self, ts: datetime) -> float:
        """
        Get session progress as a fraction (0.0 to 1.0).

        Args:
            ts: Current timestamp.

        Returns:
            Fraction of session completed.
        """
        total = (self._end - self._start).total_seconds()
        elapsed = self.get_elapsed_time(ts).total_seconds()
        return min(1.0, max(0.0, elapsed / total))

    @property
    def start(self) -> datetime:
        """Session start time."""
        return self._start

    @property
    def end(self) -> datetime:
        """Session end time."""
        return self._end

    @property
    def duration(self) -> timedelta:
        """Total session duration."""
        return self._end - self._start

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MarketSession):
            return False
        return self.session_date == other.session_date

    def __hash__(self) -> int:
        return hash(self.session_date)

    def __repr__(self) -> str:
        return f"MarketSession({self.session_date})"

    def __str__(self) -> str:
        return f"Session {self.session_date}: {self._start.strftime('%H:%M')} - {self._end.strftime('%H:%M')} IST"
