# core/api/upstox_client.py
import requests
import time
import logging
from functools import lru_cache
from datetime import datetime, date
from urllib.parse import quote_plus
from pathlib import Path
import pandas as pd

from core.config import get_access_token

logger = logging.getLogger("core.upstox_client")
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)

V2_BASE = "https://api.upstox.com/v2"
V3_BASE = "https://api.upstox.com/v3"
DEFAULT_HEADERS = {"Accept": "application/json"}

INSTR_DIR = Path("instruments/segment_wise")

def http_get(url: str, headers: dict = None, params: dict = None, timeout: int = 15, max_retries: int = 3):
    headers = headers or {}
    attempt = 0
    last_exc = None
    while attempt < max_retries:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            try:
                return resp.json()
            except Exception:
                return {"raw_text": resp.text}
        except requests.RequestException as e:
            attempt += 1
            last_exc = e
            wait = 0.5 * attempt
            logger.warning("HTTP GET failed attempt %d/%d: %s — retry in %.1fs", attempt, max_retries, e, wait)
            time.sleep(wait)
    logger.error("HTTP GET failed after %d attempts: %s", max_retries, last_exc)
    raise last_exc

class UpstoxClient:
    def __init__(self, token: str | None = None):
        self._token = token

    def _token_or_raise(self):
        if self._token:
            return self._token
        tok = get_access_token()
        if not tok:
            raise RuntimeError("No access token found. Login via Page 1.")
        self._token = tok
        return tok

    def _v2_headers(self):
        t = self._token_or_raise()
        h = DEFAULT_HEADERS.copy()
        h.update({"Authorization": f"Bearer {t}", "Api-Version": "2.0"})
        return h

    def _v3_headers(self):
        t = self._token_or_raise()
        h = {"Authorization": f"Bearer {t}", "Accept": "application/json"}
        return h

    # ----------------
    # Instruments helpers (local parquet)
    # ----------------
    @staticmethod
    def _safe_load_segment(segment_name: str):
        fn = INSTR_DIR / f"{segment_name}.parquet"
        if fn.exists():
            try:
                return pd.read_parquet(fn)
            except Exception as e:
                logger.warning("Failed to read instruments parquet %s: %s", fn, e)
        return pd.DataFrame()

    def get_instrument_key_local(self, symbol: str, segment: str = "NSE_FO"):
        """
        Look up instrument_key from local instrument parquet.
        Handles common index mappings.
        """
        if segment == "NSE_INDEX" or symbol.upper() in ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]:
            mapping = {
                "NIFTY": "NSE_INDEX|Nifty 50",
                "BANKNIFTY": "NSE_INDEX|Nifty Bank",
                "FINNIFTY": "NSE_INDEX|Nifty Fin Service",
                "MIDCPNIFTY": "NSE_INDEX|Nifty Midcap Select"
            }
            return mapping.get(symbol.upper())

        df = self._safe_load_segment(segment)
        if df.empty:
            return None
        # heuristics for column names
        cols = [c.lower() for c in df.columns]
        col_map = {c.lower(): c for c in df.columns}
        sym_col = next((c for c in cols if "trad" in c or "symbol" in c), None)
        key_col = next((c for c in cols if "instrument" in c and "key" in c or "key" in c), None)
        if not sym_col or not key_col:
            return None
        sym_col_actual = col_map[sym_col]
        key_col_actual = col_map[key_col]
        # exact match then contains
        match = df[df[sym_col_actual].astype(str).str.strip().str.upper() == str(symbol).strip().upper()]
        if not match.empty:
            return match.iloc[0][key_col_actual]
        match = df[df[sym_col_actual].astype(str).str.contains(str(symbol), case=False, na=False)]
        if not match.empty:
            return match.iloc[0][key_col_actual]
        return None

    # ----------------
    # Expiries
    # ----------------
    @lru_cache(maxsize=8)
    def get_expiries_for_underlying(self, underlying: str, segment: str = "NSE_FO"):
        """
        Prefer local instruments file. Fallback to v2 option/contract endpoint.
        Returns list of ISO dates (YYYY-MM-DD).
        """
        # local attempt
        try:
            df = self._safe_load_segment("NSE_FO")
            if not df.empty and 'trading_symbol' in df.columns:
                mask = df['trading_symbol'].astype(str).str.startswith(f"{underlying} ")
                if mask.any():
                    exps = df.loc[mask, 'expiry'].dropna().unique().tolist()
                    dates = set()
                    for x in exps:
                        try:
                            # handle numeric timestamps
                            if isinstance(x, (int,float)):
                                ts = float(x)
                                if ts > 1e11:
                                    ts = ts / 1000
                                dates.add(datetime.fromtimestamp(ts).date().isoformat())
                            else:
                                dates.add(str(x).split(" ")[0])
                        except Exception:
                            dates.add(str(x))
                    return sorted(dates)
        except Exception:
            pass

        # fallback: v2 contract
        try:
            token = self._token_or_raise()
            headers = self._v2_headers()
            inst_key_map = {
                'NIFTY': 'NSE_INDEX|Nifty 50',
                'BANKNIFTY': 'NSE_INDEX|Nifty Bank',
                'FINNIFTY': 'NSE_INDEX|Nifty Fin Service'
            }
            inst_key = inst_key_map.get(underlying.upper(), f"NSE_INDEX|{underlying}")
            url = f"{V2_BASE}/option/contract"
            params = {"instrument_key": inst_key}
            data = http_get(url, headers=headers, params=params)
            candidates = []
            # try multiple shapes
            contracts = data.get('data', {}).get('option_contracts') or data.get('data') or []
            for c in contracts:
                exp = c.get("expiry") or c.get("expiry_date") or c.get("expiryDate")
                if exp is None:
                    continue
                if isinstance(exp, (int, float)):
                    d = datetime.fromtimestamp(exp/1000).date().isoformat()
                else:
                    d = str(exp).split(" ")[0]
                candidates.append(d)
            return sorted(set(candidates))
        except Exception as e:
            logger.warning("get_expiries_for_underlying failed: %s", e)
            return []

    # ----------------
    # Option Chain
    # ----------------
    def get_option_chain(self, underlying_or_instkey: str, expiry_date: str, segment: str = "NSE_FO"):
        """
        Accepts either instrument_key (NSE_INDEX|Nifty 50) or an underlying name (NIFTY).
        Returns normalized dict: {'CE': [...], 'PE': [...], 'raw': raw_response}
        """
        # if looks like instrument_key contain '|' then treat as key
        if "|" in underlying_or_instkey:
            inst_key = underlying_or_instkey
        else:
            # try mapping common indices
            map_idx = {
                'NIFTY': 'NSE_INDEX|Nifty 50',
                'BANKNIFTY': 'NSE_INDEX|Nifty Bank',
                'FINNIFTY': 'NSE_INDEX|Nifty Fin Service'
            }
            inst_key = map_idx.get(underlying_or_instkey.upper()) or self.get_instrument_key_local(underlying_or_instkey, segment)
            if not inst_key:
                # fallback to string composition
                inst_key = f"NSE_INDEX|{underlying_or_instkey}"

        # normalise expiry param
        try:
            if isinstance(expiry_date, (int, float)):
                expiry_date = datetime.fromtimestamp(expiry_date/1000).date().isoformat()
            else:
                expiry_date = str(expiry_date).split(" ")[0]
        except:
            expiry_date = str(expiry_date)

        url = f"{V2_BASE}/option/contract"
        params = {"instrument_key": inst_key, "expiry_date": expiry_date}
        data = http_get(url, headers=self._v2_headers(), params=params)
        out = {"CE": [], "PE": [], "raw": data}
        if not data:
            return out
        contracts = data.get('data', {}).get('option_contracts', []) or data.get('data', []) or []
        for c in contracts:
            # normalize
            item = {
                'instrument_key': c.get('instrument_key') or c.get('instrumentKey') or c.get('instrument_key'),
                'strike': float(c.get('strike_price') or c.get('strike') or 0),
                'option_type': c.get('option_type') or c.get('optionType') or c.get('optType') or None,
                'ltp': float(c.get('last_price') or c.get('ltp') or 0),
                'oi': int(c.get('open_interest') or c.get('oi') or 0),
                'iv': float(c.get('iv') or 0) if c.get('iv') is not None else None,
                'bid': float(c.get('bid') or 0) if c.get('bid') is not None else None,
                'ask': float(c.get('ask') or 0) if c.get('ask') is not None else None
            }
            typ = (item['option_type'] or '').upper()
            if typ.startswith('C'):
                out['CE'].append(item)
            elif typ.startswith('P'):
                out['PE'].append(item)
        out['CE'].sort(key=lambda x: x['strike'])
        out['PE'].sort(key=lambda x: x['strike'])
        return out

    # ----------------
    # V3 historical OHLC
    # ----------------
    def fetch_ohlc(self, instrument_key: str, timeframe: str, interval_num: int, from_date, to_date):
        """
        timeframe: 'minutes'|'hours'|'days'...
        interval_num: integer
        from_date/to_date: date or datetime
        Returns raw JSON (data field contains candles)
        """
        fd = from_date.strftime("%Y-%m-%d") if hasattr(from_date, "strftime") else str(from_date)
        td = to_date.strftime("%Y-%m-%d") if hasattr(to_date, "strftime") else str(to_date)
        url = f"{V3_BASE}/historical-candle/{quote_plus(instrument_key)}/{timeframe}/{interval_num}/{td}/{fd}"
        return http_get(url, headers=self._v3_headers())

    def fetch_ltp(self, instrument_key: str):
        """
        Best-effort LTP via fetching 1-minute candles for today and taking last close.
        """
        try:
            today = datetime.utcnow().date()
            data = self.fetch_ohlc(instrument_key, "minutes", 1, today, today)
            if data and isinstance(data, dict):
                candles = data.get("data", {}).get("candles", [])
                if candles:
                    last = candles[-1]
                    # [timestamp, o,h,l,c,v,oi] per v3 format
                    return float(last[4])
        except Exception:
            pass
        return None
