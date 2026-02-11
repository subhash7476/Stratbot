import pytest
from datetime import date
from core.instruments.instrument_parser import InstrumentParser
from core.instruments.option import Option, OptionType
from core.instruments.equity import Equity
from core.instruments.instrument_base import InstrumentType


def test_parse_equity():
    symbol = "RELIANCE"
    instrument = InstrumentParser.parse(symbol)
    assert isinstance(instrument, Equity)
    assert instrument.symbol == "RELIANCE"
    assert instrument.type == InstrumentType.EQUITY
    assert instrument.multiplier == 1.0


def test_parse_option_valid():
    symbol = "NIFTY28JAN2522500CE"
    instrument = InstrumentParser.parse(symbol)

    assert isinstance(instrument, Option)
    assert instrument.symbol == symbol
    assert instrument.type == InstrumentType.OPTION
    assert instrument.underlying == "NIFTY"
    assert instrument.expiry == date(2025, 1, 28)
    assert instrument.strike == 22500.0
    assert instrument.option_type == OptionType.CALL


def test_parse_option_put():
    symbol = "BANKNIFTY30JAN2548000PE"
    instrument = InstrumentParser.parse(symbol)

    assert isinstance(instrument, Option)
    assert instrument.underlying == "BANKNIFTY"
    assert instrument.option_type == OptionType.PUT
    assert instrument.strike == 48000.0


def test_parse_invalid_format_falls_back_to_equity():
    symbol = "INVALID123"
    instrument = InstrumentParser.parse(symbol)
    assert isinstance(instrument, Equity)
    assert instrument.symbol == "INVALID123"
