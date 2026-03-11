# NiftyShield Live LTP — TLP v0.1

## Problem
`_option_price()` already has live LTP code (lines 417-431) but **silently falls back to synthetic Black-76 every time** due to a symbol format mismatch:

- `InstrumentMaster.resolve("NIFTY04MAR2622500CE")` → None (DB has `NIFTY 22500 CE 04 MAR 26`)
- Fallback `NSE_FO|NIFTY04MAR2622500CE` → not a valid Upstox key (real: `NSE_FO|54710`)
- `fetch_ltp()` fails → Black-76 fallback, no logging → silent failure

## Fix — 3 Files

### 1. `core/instruments/instrument_db.py` — Add structured field resolution
Add `resolve_option(name, expiry_date, strike, option_type) -> Optional[str]`:
- Calls existing `find_options()` internally (queries by name/expiry/strike/type — works)
- Returns just the `instrument_key` string (e.g. `NSE_FO|54710`)
- Single convenience method, no new abstractions

### 2. `core/strategies/nifty_shield_strategy.py` — Use cached instrument keys
- Add `_ce_ikey: Optional[str]` and `_pe_ikey: Optional[str]` session fields (init to None)
- **At entry** (`_enter()`): resolve both keys via `_instrument_db.resolve_option("NIFTY", expiry, strike, "CE"/"PE")`, cache in `_ce_ikey`/`_pe_ikey`
- **At adjustment** (`_adjust_leg()`): re-resolve for the new strike, update cached key
- **At reset** (`_reset_session()`): clear both keys to None
- **`_option_price()`**: use `_ce_ikey`/`_pe_ikey` directly instead of re-resolving every cycle
- **Logging**: `[NiftyShield] LIVE LTP: CE NSE_FO|54710 → Rs 125.5` or `[NiftyShield] SYNTHETIC: CE 22500 → Rs 120.3`

### 3. `core/brokers/upstox_market_data.py` — Add batch LTP
Add `fetch_ltp_batch(keys: list[str]) -> dict[str, float]`:
- Upstox V2 supports comma-separated keys: `GET /v2/market-quote/quotes?instrument_key=K1,K2`
- Returns `{instrument_key: ltp}` dict
- Single API call for both CE+PE (halves request count)
- `_option_price()` still calls single `fetch_ltp()` per leg (simpler), but batch is available for future use

## What Does NOT Change
- Backtest mode — still uses Black-76 (no live data)
- `OptionsContractSelector` — symbol format stays as-is (used for display/DB logging)
- `_enter()` / `_manage()` / `_close()` flow — unchanged, just gets real prices instead of synthetic
- Instrument master refresh — already fixed today (38K rows, PyArrow bulk insert)

## Expected Behavior After Fix
- 13:05 entry: resolves ATM CE+PE instrument_keys from master, fetches real LTP for premium
- Every 30s management: fetches real CE+PE LTP for P&L, stop/target/delta checks
- If Upstox API fails: falls back to Black-76 (same as today, but now logged)
- Dashboard shows real market premiums instead of theoretical
