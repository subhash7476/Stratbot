import re
from datetime import datetime
from core.instruments.instrument_base import Instrument
from core.instruments.option import Option, OptionType
from core.instruments.equity import Equity


class InstrumentParser:
    # Regex for NIFTY28JAN2522500CE
    # Groups: Underlying, Day, Month, Year, Strike, Type
    OPTION_REGEX = re.compile(
        r"^([A-Z]+)(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")

    @staticmethod
    def parse(symbol: str) -> Instrument:
        """
        Parses a symbol string into an Instrument object.
        Tries Option format first, falls back to Equity.
        """
        # Try Option
        match = InstrumentParser.OPTION_REGEX.match(symbol)
        if match:
            try:
                underlying, day, month_str, year, strike, opt_type = match.groups()

                # Parse date (e.g., 28JAN25)
                expiry_str = f"{day}{month_str}{year}"
                expiry = datetime.strptime(expiry_str, "%d%b%y").date()

                option_type = OptionType(opt_type)
                strike_price = float(strike)

                return Option(
                    symbol=symbol,
                    underlying=underlying,
                    expiry=expiry,
                    strike=strike_price,
                    option_type=option_type,
                    lot_size=1,
                    multiplier=1.0
                )
            except ValueError:
                pass

        # Default to Equity
        return Equity(symbol=symbol)
