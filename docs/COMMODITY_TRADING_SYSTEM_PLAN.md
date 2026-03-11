# Commodity Trading System Implementation Plan

## 1. Overview
The platform currently handles equities and options natively via Upstox and DuckDB. Since the core execution and backtesting logic is generalized, we can integrate an MCX (Commodity) trading system seamlessly without changing the core mechanics (Strategy is dumb, Execution owns reality).

We will focus on implementing a new trend-following strategy designed specifically for high-liquidity, high-volatility commodities: **Crude Oil** and **Gold**.

## 2. Architecture & Data Flow
1. **Universe Update**: Add `MCX_FO|...` symbols to `config/market_universe.json`.
2. **Strategy (`CommodityTrendStrategy`)**: A volatility-breakout or moving-average-crossover strategy isolated in `core/strategies/commodity_trend.py`.
3. **Execution**: The execution engine natively handles signals. However, commodity lot sizes and tick sizes must be correctly mapped when configuring risk models.
4. **Flask Blueprint (`commodities_bp`)**: A standalone plug-and-play UI blueprint at `flask_app/blueprints/commodities.py` showing live commodity signals, open positions, and market state.
5. **App Facade**: `app_facade/commodities_facade.py` acts as the bridge between DuckDB state/telemetry and the Flask UI.

## 3. Implementation Steps (Plug and Play)

### Phase 1: New Files (No permission needed)

1. **`core/strategies/commodity_trend.py`**
   - Implements `CommodityTrendStrategy` extending the base strategy class.
   - Computes trend conditions (e.g., dual EMA crossover + ATR volatility).
   - Emits `SignalEvent` objects strictly.

2. **`scripts/commodity_runner.py`**
   - Live daemon script to orchestrate the commodity strategy.
   - Connects to Upstox, fetches recent 15m/1h candles, feeds `CommodityTrendStrategy`, and routes signals to execution engine.

3. **`app_facade/commodities_facade.py`**
   - Read-only data access layer for the UI.
   - Fetches active commodity positions, latest signals, and overall PnL from the local database.

4. **`flask_app/blueprints/commodities.py`**
   - New blueprint mapped to `/commodities`.
   - API endpoints for the frontend to poll for state updates.

5. **`flask_app/templates/commodities/index.html`**
   - A Tailwind-styled UI for visualizing commodity strategy state, active trades, and recent performance.

### Phase 2: Configuration Updates (Requires Permission)

1. **`config/market_universe.json`**
   - Add MCX instruments (e.g., Active Crude Oil and Gold symbols).

2. **`flask_app/__init__.py`**
   - Register the new blueprint:
     ```python
     from flask_app.blueprints.commodities import commodities_bp
     app.register_blueprint(commodities_bp)
     ```

3. **`flask_app/templates/base.html`**
   - Add navigation link to the sidebar:
     ```html
     <a href="{{ url_for('commodities.index') }}" class="sidebar-link flex items-center px-4 py-3 rounded-lg text-slate-400 {% if request.endpoint and request.endpoint.startswith('commodities') %}active{% endif %}">
         <i class="fas fa-oil-can w-6"></i>
         <span class="font-medium">Commodities</span>
     </a>
     ```

## 4. Why this approach?
- **Zero Impact on Core**: Existing Nifty strategies, Options dashboard, and backtest runners remain completely untouched.
- **Audit-First**: By adhering to the `SignalEvent` standard, every trade taken by the commodity strategy is perfectly auditable in your DuckDB tables.
- **UI Independence**: The `commodities` blueprint and facade decouple the UI from the underlying trading script. 

## Requesting Permission
To proceed with implementation, I need your permission to:
1. Generate the new files described in Phase 1.
2. Modify `config/market_universe.json`, `flask_app/__init__.py`, and `flask_app/templates/base.html` as outlined in Phase 2.