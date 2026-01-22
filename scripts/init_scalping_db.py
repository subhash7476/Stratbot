 
"""
₹500 Scalping Engine - Database Initialization
Creates all required tables for the scalping system
"""

import duckdb
import sys
from pathlib import Path
from datetime import date

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "trading_bot.db"

def init_scalping_tables():
    """Initialize all tables for the scalping engine"""
    
    conn = duckdb.connect(str(DB_PATH))
    
    print("🔧 Initializing Scalping Engine Database...")
    
    # Table 1: Daily Risk Log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_risk_log (
            date DATE PRIMARY KEY,
            trades_count INT DEFAULT 0,
            wins INT DEFAULT 0,
            losses INT DEFAULT 0,
            consecutive_losses INT DEFAULT 0,
            daily_pnl DECIMAL(10,2) DEFAULT 0,
            locked_out BOOLEAN DEFAULT FALSE,
            lockout_reason VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ Created: daily_risk_log")
    
    # Table 2: Live Positions
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_positions (
            position_id VARCHAR PRIMARY KEY,
            symbol VARCHAR NOT NULL,
            entry_time TIMESTAMP NOT NULL,
            entry_premium DECIMAL(10,4) NOT NULL,
            lot_size INT NOT NULL,
            strike INT NOT NULL,
            option_type VARCHAR(2) NOT NULL,  -- CE/PE
            sl_premium DECIMAL(10,4) NOT NULL,
            target1_premium DECIMAL(10,4) NOT NULL,
            target2_premium DECIMAL(10,4) NOT NULL,
            partial_booked BOOLEAN DEFAULT FALSE,
            status VARCHAR DEFAULT 'ACTIVE',  -- ACTIVE/EXITED
            exit_time TIMESTAMP,
            exit_premium DECIMAL(10,4),
            exit_reason VARCHAR,  -- SL/TIME/TARGET1/TARGET2
            pnl DECIMAL(10,2),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ Created: live_positions")
    
    # Table 3: Scanner Signals History
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_signals (
            signal_id VARCHAR PRIMARY KEY,
            timestamp TIMESTAMP NOT NULL,
            symbol VARCHAR NOT NULL,
            filter1_index BOOLEAN,
            filter2_stock BOOLEAN,
            filter3_impulse BOOLEAN,
            filter4_option BOOLEAN,
            filter5_feasibility BOOLEAN,
            passed_all BOOLEAN,
            failed_at VARCHAR,  -- Which filter failed first
            signal_strength DECIMAL(3,2),  -- 0-1 score
            executed BOOLEAN DEFAULT FALSE,
            metadata JSON,  -- Store filter details
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("✅ Created: scanner_signals")
    
    # Table 4: Filter Performance Stats
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filter_stats (
            date DATE NOT NULL,
            filter_name VARCHAR NOT NULL,
            stocks_scanned INT DEFAULT 0,
            stocks_passed INT DEFAULT 0,
            pass_rate DECIMAL(5,2),
            avg_scan_time_ms DECIMAL(8,2),
            PRIMARY KEY (date, filter_name)
        )
    """)
    print("✅ Created: filter_stats")
    
    # Table 5: Stock Universe Configuration
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe (
            symbol VARCHAR PRIMARY KEY,
            tier INT,  -- 1=High priority, 2=Medium, 3=Low
            enabled BOOLEAN DEFAULT TRUE,
            avg_impulses_per_day DECIMAL(4,2),
            win_rate DECIMAL(5,2),
            avg_profit DECIMAL(10,2),
            last_scanned TIMESTAMP,
            notes VARCHAR
        )
    """)
    print("✅ Created: stock_universe")
    
    # Initialize today's risk log entry
    today = date.today()
    conn.execute("""
        INSERT INTO daily_risk_log (date)
        VALUES (?)
        ON CONFLICT (date) DO NOTHING
    """, [today])
    print(f"✅ Initialized risk log for {today}")
    
    # Insert default stock universe (20 stocks from your selection)
    default_stocks = [
        # Tier 1: High lot size (Easy ₹500)
        ('PNB', 1), ('TATASTEEL', 1), ('TATAMOTORS', 1), 
        ('COALINDIA', 1), ('HINDALCO', 1),
        
        # Tier 2: Medium lot size
        ('SBIN', 2), ('ICICIBANK', 2), ('AXISBANK', 2),
        ('INFY', 2), ('WIPRO', 2), ('JSWSTEEL', 2),
        
        # Tier 3: Lower lot but high quality
        ('HDFCBANK', 3), ('TCS', 3), ('RELIANCE', 3),
        ('BHARTIARTL', 3), ('ITC', 3), ('SUNPHARMA', 3),
        ('M&M', 3), ('ONGC', 3), ('LT', 3)
    ]
    
    for symbol, tier in default_stocks:
        conn.execute("""
            INSERT INTO stock_universe (symbol, tier)
            VALUES (?, ?)
            ON CONFLICT (symbol) DO NOTHING
        """, [symbol, tier])
    
    print(f"✅ Inserted {len(default_stocks)} stocks into universe")
    
    # Create indexes for performance
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON scanner_signals(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON scanner_signals(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON live_positions(status)")
    print("✅ Created indexes")
    
    # Verify tables
    tables = conn.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'main' 
        AND table_name IN (
            'daily_risk_log', 'live_positions', 'scanner_signals', 
            'filter_stats', 'stock_universe'
        )
    """).fetchall()
    
    print(f"\n📊 Database ready with {len(tables)} scalping tables:")
    for table in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
        print(f"   • {table[0]}: {count} rows")
    
    conn.close()
    print("\n✅ Database initialization complete!")

if __name__ == "__main__":
    init_scalping_tables()