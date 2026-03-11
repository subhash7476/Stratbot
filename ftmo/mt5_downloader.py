"""MT5 data downloader — connects to MetaTrader 5 and pulls OHLCV bars.

Usage (via CLI):
    python -m ftmo.cli download --login 12345 --password mypass --server FTMO-Demo2 \\
        --symbol XAUUSD --timeframe M5 --start 2024-01-01

Requires: pip install MetaTrader5
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

IST = "Asia/Kolkata"

_TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


def _get_mt5():
    try:
        import MetaTrader5 as mt5
        return mt5
    except ImportError:
        raise ImportError(
            "MetaTrader5 package not installed.\n"
            "Run: pip install MetaTrader5\n"
            "Note: requires Windows + MT5 terminal installed."
        )


def _resolve_timeframe(mt5, tf: str):
    if tf not in _TIMEFRAME_MAP:
        raise ValueError(f"Unknown timeframe '{tf}'. Valid: {list(_TIMEFRAME_MAP)}")
    return getattr(mt5, _TIMEFRAME_MAP[tf])


class MT5Downloader:
    def __init__(self, login: int, password: str, server: str):
        self.login = login
        self.password = password
        self.server = server
        self._connected = False

    def connect(self):
        mt5 = _get_mt5()
        if not mt5.initialize():
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

        if not mt5.login(self.login, password=self.password, server=self.server):
            mt5.shutdown()
            raise RuntimeError(
                f"MT5 login failed: {mt5.last_error()}\n"
                f"Check login={self.login}, server={self.server}"
            )

        info = mt5.account_info()
        logger.info(
            f"MT5 connected: login={info.login} server={info.server} "
            f"balance={info.balance:.2f} {info.currency}"
        )
        print(
            f"Connected: account {info.login} @ {info.server} | "
            f"Balance: {info.balance:,.2f} {info.currency}"
        )
        self._connected = True

    def disconnect(self):
        try:
            mt5 = _get_mt5()
            mt5.shutdown()
        except Exception:
            pass
        self._connected = False

    def download(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Download bars and return IST-timestamped DataFrame."""
        if not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        mt5 = _get_mt5()
        tf = _resolve_timeframe(mt5, timeframe)

        # MT5 copy_rates_range expects UTC-aware datetimes
        start_utc = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
        end_utc = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end.astimezone(timezone.utc)

        logger.info(f"Requesting {symbol} {timeframe}: {start_utc} → {end_utc} (UTC)")
        rates = mt5.copy_rates_range(symbol, tf, start_utc, end_utc)

        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"No data returned for {symbol} {timeframe}.\n"
                f"Error: {mt5.last_error()}\n"
                f"Check the symbol name is correct in your MT5 terminal."
            )

        df = pd.DataFrame(rates)
        # MT5 'time' field = Unix seconds (UTC)
        df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST)

        result = pd.DataFrame({
            "timestamp": df["timestamp"],
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
            "volume": df["tick_volume"].astype(float),
        }).sort_values("timestamp").reset_index(drop=True)

        logger.info(
            f"Downloaded {len(result)} bars: "
            f"{result['timestamp'].iloc[0]} → {result['timestamp'].iloc[-1]}"
        )
        return result

    def download_and_save(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: Optional[str],
        out_dir: Path,
    ) -> pd.DataFrame:
        """Download, save CSV + parquet cache, return DataFrame."""
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end) if end else datetime.utcnow()

        df = self.download(symbol, timeframe, start_dt, end_dt)

        csv_path = out_dir / f"{symbol}_{timeframe}.csv"
        parquet_path = out_dir / "cache_m5.parquet"

        df.to_csv(str(csv_path), index=False)
        df.to_parquet(str(parquet_path), index=False)

        print(f"Saved {len(df)} bars to:")
        print(f"  CSV:     {csv_path}")
        print(f"  Parquet: {parquet_path}  ← backtest reads this")
        return df
