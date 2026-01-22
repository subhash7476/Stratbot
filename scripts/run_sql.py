import duckdb
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trading_bot.duckdb"

con = duckdb.connect(str(DB_PATH))

sql = """
SELECT
    COUNT(DISTINCT instrument_key) AS unique_instrument_keys,
    COUNT(*) AS total_rows
FROM ohlcv_1m;


"""

df = con.execute(sql).df()
con.close()

print(df)
