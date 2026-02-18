
from pathlib import Path
from datetime import datetime
from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery

db = DatabaseManager(Path("data"))
query = MarketDataQuery(db)

symbol = "NSE_EQ|INE205A01025" # Known good symbol based on previous output
start = datetime(2024, 10, 17)
end = datetime(2024, 10, 20) # Short range

print(f"Querying {symbol} from {start} to {end}...")
try:
    df = query.get_ohlcv(symbol, start, end, "15m")
    print(f"Result DataFrame Shape: {df.shape}")
    if not df.empty:
        print(df.head())
    else:
        print("Empty DataFrame returned.")
except Exception as e:
    print(f"Error querying data: {e}")
