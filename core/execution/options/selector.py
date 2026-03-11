from datetime import date, timedelta
from typing import Optional, Union
from datetime import datetime

from core.instruments.option import Option, OptionType
from core.events import SignalType


# NSE index lot sizes (post-2024 SEBI revision)
INDEX_LOT_SIZES = {
    "NSE_INDEX|Nifty 50": 75,
    "NSE_INDEX|Nifty Bank": 35,
}

# ATM strike step sizes per index
INDEX_STRIKE_STEPS = {
    "NSE_INDEX|Nifty 50": 50,
    "NSE_INDEX|Nifty Bank": 100,
}

# Short names used in NSE option symbol strings
INDEX_SHORT_NAMES = {
    "NSE_INDEX|Nifty 50": "NIFTY",
    "NSE_INDEX|Nifty Bank": "BANKNIFTY",
}

# Weekly expiry weekday per index (SEBI post-2024 schedule)
# Python weekday: 0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday
INDEX_EXPIRY_WEEKDAY = {
    "NSE_INDEX|Nifty 50": 1,      # Tuesday
    "NSE_INDEX|Nifty Bank": 2,    # Wednesday
    "NSE_INDEX|FINNIFTY": 1,      # Tuesday
    "NSE_INDEX|MIDCPNIFTY": 0,    # Monday
}


class OptionsContractSelector:
    """
    Translates an underlying signal into a specific NSE index option contract.

    Expiry rule: nearest Thursday with >= expiry_days_min days remaining.
    Strike rule: ATM = round(underlying_price / step) * step.
    Direction:   BUY  → CALL (CE)
                 SELL → PUT  (PE)

    Symbol format: {UNDERLYING}{DD}{MON}{YY}{STRIKE}{CE/PE}
    Example:       NIFTY26FEB2522500CE

    Usage
    -----
    When a strategy emits a SignalEvent with metadata["execution_mode"] = "option",
    the ExecutionHandler calls:

        instrument = selector.select(
            underlying    = signal.symbol,          # "NSE_INDEX|Nifty 50"
            underlying_price = current_price,
            direction     = signal.signal_type,
            timestamp     = signal.timestamp,
            policy        = signal.metadata.get("option_policy", {}),
        )

    The returned Option has the correct symbol, lot_size, expiry, and strike.
    The handler then records the position under the option symbol (e.g.
    "NIFTY26FEB2522500CE").  EXIT signals must carry that option symbol,
    not the underlying — the strategy caller is responsible for tracking it
    from the NormalizedOrder returned by process_signal().
    """

    def select(
        self,
        underlying: str,
        underlying_price: float,
        direction: SignalType,
        timestamp: Union[datetime, date],
        policy: Optional[dict] = None,
    ) -> Option:
        policy = policy or {}

        expiry_days_min = policy.get("expiry_days_min", 2)
        step = policy.get("strike_step") or INDEX_STRIKE_STEPS.get(underlying, 50)
        lot_size = policy.get("lot_size_override") or INDEX_LOT_SIZES.get(underlying, 50)

        from_date = timestamp.date() if isinstance(timestamp, datetime) else timestamp
        # Allow caller to pin to a specific expiry date (e.g., leg adjustments stay on same week)
        expiry = policy.get("expiry_date")
        if expiry is None:
            expiry_weekday = INDEX_EXPIRY_WEEKDAY.get(underlying, 1)  # default Tuesday
            expiry = self._nearest_expiry(from_date, expiry_days_min, expiry_weekday)
        strike = self._round_to_strike(underlying_price, step)
        option_type = OptionType.CALL if direction == SignalType.BUY else OptionType.PUT

        short_name = INDEX_SHORT_NAMES.get(
            underlying,
            underlying.split("|")[-1].upper().replace(" ", ""),
        )
        symbol = self._build_symbol(short_name, expiry, strike, option_type)

        return Option(
            symbol=symbol,
            underlying=underlying,
            expiry=expiry,
            strike=float(strike),
            option_type=option_type,
            lot_size=lot_size,
            multiplier=1.0,
        )

    def _nearest_expiry(self, from_date: date, min_days: int, expiry_weekday: int = 1) -> date:
        """Nearest expiry weekday that is at least min_days away from from_date."""
        target = from_date + timedelta(days=min_days)
        days_ahead = (expiry_weekday - target.weekday()) % 7
        return target + timedelta(days=days_ahead)

    def _round_to_strike(self, price: float, step: int) -> int:
        return int(round(price / step) * step)

    def _build_symbol(
        self, short_name: str, expiry: date, strike: int, option_type: OptionType
    ) -> str:
        day = expiry.strftime("%d")
        month = expiry.strftime("%b").upper()
        year = expiry.strftime("%y")
        return f"{short_name}{day}{month}{year}{strike}{option_type.value}"
