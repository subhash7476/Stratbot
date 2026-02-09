"""
Market Hours Utility
--------------------
Utilities for checking market hours and trading sessions.

Indian market hours (IST):
- Pre-market: 9:00 AM - 9:15 AM
- Market open: 9:15 AM - 3:30 PM
- Post-market: 3:30 PM - 4:00 PM
"""

from datetime import datetime, time, timedelta
from typing import Tuple, Optional
import pytz


class MarketHours:
    """
    Utility class for Indian market hours.

    All times are in IST (Indian Standard Time).

    Usage:
        from core.database.utils import MarketHours

        # Check if market is open
        if MarketHours.is_market_open():
            print("Market is open")

        # Get current IST time
        now = MarketHours.get_ist_now()

        # Get today's session times
        open_time, close_time = MarketHours.get_session_times()
    """

    # Indian Standard Time timezone
    IST = pytz.timezone("Asia/Kolkata")

    # Market hours (IST)
    MARKET_OPEN = time(9, 15)  # 9:15 AM
    MARKET_CLOSE = time(15, 30)  # 3:30 PM
    PRE_MARKET_OPEN = time(9, 0)  # 9:00 AM
    POST_MARKET_CLOSE = time(16, 0)  # 4:00 PM

    # Trading days (Monday = 0, Sunday = 6)
    TRADING_DAYS = {0, 1, 2, 3, 4}  # Monday to Friday

    @classmethod
    def get_ist_now(cls) -> datetime:
        """
        Get current time in IST.

        Returns:
            Current datetime in IST timezone.
        """
        return datetime.now(cls.IST)

    @classmethod
    def to_ist(cls, dt: datetime) -> datetime:
        """
        Convert a datetime to IST.

        Args:
            dt: Datetime to convert (can be naive or aware).

        Returns:
            Datetime in IST timezone.
        """
        if dt.tzinfo is None:
            # Assume naive datetime is already IST
            return cls.IST.localize(dt)
        return dt.astimezone(cls.IST)

    @classmethod
    def is_trading_day(cls, dt: Optional[datetime] = None) -> bool:
        """
        Check if a date is a trading day (weekday).

        Args:
            dt: Datetime to check. Defaults to current IST time.

        Returns:
            True if it's a trading day (Mon-Fri).
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        return dt.weekday() in cls.TRADING_DAYS

    @classmethod
    def is_market_open(cls, dt: Optional[datetime] = None) -> bool:
        """
        Check if the market is currently open.

        Args:
            dt: Datetime to check. Defaults to current IST time.

        Returns:
            True if market is open (9:15 AM - 3:30 PM IST on weekdays).
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        # Check if it's a trading day
        if not cls.is_trading_day(dt):
            return False

        # Check time
        current_time = dt.time()
        return cls.MARKET_OPEN <= current_time < cls.MARKET_CLOSE

    @classmethod
    def is_pre_market(cls, dt: Optional[datetime] = None) -> bool:
        """
        Check if we're in pre-market hours.

        Args:
            dt: Datetime to check. Defaults to current IST time.

        Returns:
            True if in pre-market (9:00 AM - 9:15 AM IST).
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        if not cls.is_trading_day(dt):
            return False

        current_time = dt.time()
        return cls.PRE_MARKET_OPEN <= current_time < cls.MARKET_OPEN

    @classmethod
    def is_post_market(cls, dt: Optional[datetime] = None) -> bool:
        """
        Check if we're in post-market hours.

        Args:
            dt: Datetime to check. Defaults to current IST time.

        Returns:
            True if in post-market (3:30 PM - 4:00 PM IST).
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        if not cls.is_trading_day(dt):
            return False

        current_time = dt.time()
        return cls.MARKET_CLOSE <= current_time < cls.POST_MARKET_CLOSE

    @classmethod
    def get_session_times(
        cls, dt: Optional[datetime] = None
    ) -> Tuple[datetime, datetime]:
        """
        Get today's market open and close times.

        Args:
            dt: Date to get session for. Defaults to today.

        Returns:
            Tuple of (open_datetime, close_datetime) in IST.
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        date = dt.date()
        open_dt = cls.IST.localize(datetime.combine(date, cls.MARKET_OPEN))
        close_dt = cls.IST.localize(datetime.combine(date, cls.MARKET_CLOSE))

        return (open_dt, close_dt)

    @classmethod
    def get_next_market_open(cls, dt: Optional[datetime] = None) -> datetime:
        """
        Get the next market open time.

        Args:
            dt: Starting datetime. Defaults to current IST time.

        Returns:
            Datetime of next market open in IST.
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        # Start checking from today's open
        check_date = dt.date()
        check_dt = cls.IST.localize(datetime.combine(check_date, cls.MARKET_OPEN))

        # If today's open has passed, start from tomorrow
        if dt >= check_dt:
            check_date += timedelta(days=1)
            check_dt = cls.IST.localize(datetime.combine(check_date, cls.MARKET_OPEN))

        # Find next trading day
        while check_dt.weekday() not in cls.TRADING_DAYS:
            check_date += timedelta(days=1)
            check_dt = cls.IST.localize(datetime.combine(check_date, cls.MARKET_OPEN))

        return check_dt

    @classmethod
    def time_until_market_open(cls, dt: Optional[datetime] = None) -> timedelta:
        """
        Get time remaining until market opens.

        Args:
            dt: Starting datetime. Defaults to current IST time.

        Returns:
            Timedelta until next market open.
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        next_open = cls.get_next_market_open(dt)
        return next_open - dt

    @classmethod
    def time_until_market_close(cls, dt: Optional[datetime] = None) -> Optional[timedelta]:
        """
        Get time remaining until market closes.

        Args:
            dt: Starting datetime. Defaults to current IST time.

        Returns:
            Timedelta until market close, or None if market is not open.
        """
        if dt is None:
            dt = cls.get_ist_now()
        else:
            dt = cls.to_ist(dt)

        if not cls.is_market_open(dt):
            return None

        _, close_dt = cls.get_session_times(dt)
        return close_dt - dt
