# Upstox V3 Historical Data Fetcher & Database Viewer

## Overview

This implementation enhances the Database page with Upstox V3 historical candle data ingestion and a comprehensive data viewer UI. The system allows users to fetch, preview, persist, browse, and export historical market data while maintaining strict architectural boundaries.

## Architecture

The system enforces a strict separation of concerns following the pattern:
```
CLI / Controlled API → DuckDB → Facade → Flask UI
```

### Non-Negotiables
- Flask routes must not call broker APIs directly
- Flask routes must not write to DuckDB directly
- UI actions may only request orchestration, never perform ingestion
- DuckDB is the single source of truth
- All ingestion must be auditable, idempotent, deterministic

## Components

### 1. Core API Layer
**File:** `core/api/upstox_client.py`

Added `fetch_historical_candles_v3()` method supporting:
- Both V2 and V3 base URLs
- Parsing candles into structured dictionaries
- Converting timestamps safely (UTC → IST)
- Handling Upstox error codes explicitly

### 2. Ingestion Control Layer
**File:** `scripts/fetch_upstox_historical.py`

- Validates parameters before fetching
- Calls `UpstoxClient.fetch_historical_candles_v3`
- Inserts into DuckDB atomically
- Enforces idempotency
- Logs missing candles (does not fill gaps)

### 3. Database Schema
**File:** `core/data/schema.py`

Uses existing `ohlcv_1m` table instead of creating a new table:
- Maintains existing schema compatibility
- Stores instrument_key, timestamp, open, high, low, close, volume
- Preserves existing indexes and constraints

### 4. Flask Blueprint
**File:** `flask_app/blueprints/database.py`

Added three new API endpoints:
- `POST /api/request-historical-fetch` - Initiates data ingestion
- `GET /api/historical-data` - Queries DuckDB read-only
- `GET /api/instruments-search` - Searches for instruments

### 5. Frontend UI
**File:** `flask_app/templates/database/index.html`

New sections added:
- **Upstox Data Fetcher Card** - Glass UI for data fetching
- **Data Viewer Panel** - Table view with sorting and pagination
- **Saved Data Browser** - Instrument selector and date filters

## Features

### Fetch Card (Glass UI)
- Instrument autocomplete with suggestions
- Unit selector (minutes, hours, days, weeks, months)
- Interval validation based on unit
- Date range picker with validation

### Data Viewer Panel
- Table view with sortable columns (Timestamp, O, H, L, C, Volume)
- Pagination (50 per page)
- Color-coded price moves
- CSV export functionality

### Validation Rules
- **Interval Rules:**
  - Minutes: 1-300
  - Hours: 1-5
  - Days/Weeks/Months: 1 only
- **Date Limits:**
  - ≤15m: max 1 month
  - Hours: max 1 quarter
  - Days: max 10 years
- **Instrument Format:** `{EXCHANGE}_{SEGMENT}|{ISIN}`

### Error Handling
- Specific Upstox error code handling
- User-friendly error messages
- Network error retry options
- Token expiration redirects

## Security Constraints
- Parameterized DuckDB queries only
- Rate limit: 10 requests/min/user
- Never expose access token to JS
- Sanitized instrument keys
- No background execution inside Flask workers

## Usage

### CLI Usage
```bash
python scripts/fetch_upstox_historical.py \
  --instrument_key NSE_EQ|INE848E01016 \
  --unit days \
  --interval 1 \
  --from 2024-01-01 \
  --to 2024-01-31
```

### UI Workflow
1. Search and select an instrument
2. Choose time unit and interval
3. Set date range
4. Click "Preview" to see data or "Save to DB" to persist
5. Browse and export saved data using the data viewer

## Definition of Done
- ✅ Data fetched only via controlled ingestion
- ✅ UI is read-only observer
- ✅ DuckDB contains deduplicated candles in existing ohlcv_1m table
- ✅ Errors handled cleanly
- ✅ UI matches glass design system
- ✅ Zero architectural violations

## Reminder
> The UI observes reality. It never creates it.