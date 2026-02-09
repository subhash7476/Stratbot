from core.data.duckdb_client import db_cursor

def check_db():
    with db_cursor(read_only=True) as conn:
        print("Tables:")
        tables = conn.execute("SHOW TABLES").fetchall()
        for t in tables:
            print(f" - {t[0]}")
        
        print("\nInstruments sample (first 5):")
        try:
            instruments = conn.execute("SELECT * FROM instruments LIMIT 5").fetchdf()
            print(instruments)
        except Exception as e:
            print(f"Error reading instruments: {e}")

        print("\nFO Stocks Master sample (first 5):")
        try:
            fo = conn.execute("SELECT * FROM fo_stocks_master LIMIT 5").fetchdf()
            print(fo)
        except Exception as e:
            print(f"Error reading fo_stocks_master: {e}")

        print("\nQuery for INFY in instruments:")
        try:
            infy = conn.execute("SELECT instrument_key, exchange, trading_symbol FROM instruments WHERE trading_symbol = 'INFY'").fetchdf()
            print(infy)
        except Exception as e:
            print(f"Error querying INFY: {e}")

if __name__ == "__main__":
    check_db()
