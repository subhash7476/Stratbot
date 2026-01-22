def run_backtest(df, capital=200000, risk_pct=2.0):
    cash = capital
    positions = []
    closed = []

    for i in range(1, len(df)):
        signal = df["Signal"].iloc[i]
        price = df["Close"].iloc[i]

        # Close existing positions
        for p in positions[:]:
            if p["side"] == "LONG" and price <= p["sl"]:
                pnl = (price - p["entry"]) * p["qty"]
                cash += p["qty"] * price
                closed.append({"entry": p["entry"], "exit": price, "pnl": pnl})
                positions.remove(p)
            elif p["side"] == "SHORT" and price >= p["sl"]:
                pnl = (p["entry"] - price) * p["qty"]
                cash += p["qty"] * price
                closed.append({"entry": p["entry"], "exit": price, "pnl": pnl})
                positions.remove(p)

        # New entry
        if signal != 0 and len(positions) == 0:
            sl_dist = df["ATR"].iloc[i] * 2
            qty = int((cash * (risk_pct / 100)) / sl_dist)
            qty = max(1, qty)

            if signal == 1:
                positions.append({"side": "LONG", "entry": price, "sl": price - sl_dist, "qty": qty})
                cash -= qty * price
            elif signal == -1:
                positions.append({"side": "SHORT", "entry": price, "sl": price + sl_dist, "qty": qty})
                cash -= qty * price

    return closed
