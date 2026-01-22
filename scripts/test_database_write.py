"""
Database Write Test Script
Tests if DuckDB can write to the database file
"""

import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.database import TradingDB

def test_database_write():
    """Test database write operations"""
    
    print("=" * 80)
    print("DATABASE WRITE TEST")
    print("=" * 80)
    print()
    
    try:
        # Try to connect
        print("1. Attempting to connect to database...")
        db = TradingDB()
        print("   ✅ Connection successful")
        
        # Try to read
        print("\n2. Testing read operation...")
        count = db.con.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        print(f"   ✅ Read successful: {count:,} instruments")
        
        # Try to write (small test)
        print("\n3. Testing write operation...")
        test_table_sql = """
        CREATE TABLE IF NOT EXISTS test_write (
            id INTEGER,
            test_text VARCHAR,
            created_at TIMESTAMP
        )
        """
        db.con.execute(test_table_sql)
        print("   ✅ Table creation successful")
        
        # Insert test row
        print("\n4. Testing insert operation...")
        db.con.execute("""
            INSERT INTO test_write VALUES (1, 'test', ?)
        """, [datetime.now()])
        print("   ✅ Insert successful")
        
        # Commit
        print("\n5. Testing commit operation...")
        db.con.commit()
        print("   ✅ Commit successful")
        
        # Verify
        print("\n6. Verifying write...")
        test_count = db.con.execute("SELECT COUNT(*) FROM test_write").fetchone()[0]
        print(f"   ✅ Verified: {test_count} test rows")
        
        # Cleanup
        print("\n7. Cleaning up test table...")
        db.con.execute("DROP TABLE test_write")
        print("   ✅ Cleanup successful")
        
        db.con.close()
        
        print("\n" + "=" * 80)
        print("✅ ALL TESTS PASSED")
        print("=" * 80)
        print("\nDatabase is healthy and writable!")
        print("The hang might be due to large batch size.")
        print()
        print("RECOMMENDATION:")
        print("  Try downloading instruments again.")
        print("  It should work now.")
        
        return True
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        print()
        print("=" * 80)
        print("DIAGNOSIS:")
        print("=" * 80)
        
        error_str = str(e).lower()
        
        if "locked" in error_str or "busy" in error_str:
            print("\n🔒 DATABASE LOCKED")
            print()
            print("Cause: Another process is using the database")
            print()
            print("Solutions:")
            print("  1. Close ALL Streamlit windows")
            print("  2. Kill all Python processes:")
            print("     taskkill /F /IM python.exe")
            print("  3. Wait 30 seconds")
            print("  4. Try again")
        
        elif "corrupt" in error_str or "checksum" in error_str:
            print("\n💥 DATABASE CORRUPTED")
            print()
            print("Cause: File integrity compromised")
            print()
            print("Solution:")
            print("  1. Backup: copy trading_bot.duckdb trading_bot_backup.duckdb")
            print("  2. Delete: del trading_bot.duckdb")
            print("  3. Restart Streamlit")
            print("  4. Download instruments again")
        
        elif "permission" in error_str or "access" in error_str:
            print("\n🚫 PERMISSION DENIED")
            print()
            print("Cause: No write access to database file")
            print()
            print("Solutions:")
            print("  1. Check if file is read-only")
            print("  2. Run as Administrator")
            print("  3. Check antivirus/firewall")
            print("  4. Check disk space")
        
        elif "disk" in error_str or "space" in error_str:
            print("\n💾 DISK SPACE ISSUE")
            print()
            print("Cause: Not enough free disk space")
            print()
            print("Solutions:")
            print("  1. Check free space on D: drive")
            print("  2. Need at least 2GB free")
            print("  3. Delete old backups")
        
        else:
            print("\n❓ UNKNOWN ERROR")
            print()
            print("Error details:")
            print(f"  {e}")
            print()
            print("Generic solutions:")
            print("  1. Restart computer (clears locks)")
            print("  2. Check disk health: chkdsk D: /f")
            print("  3. Try different location")
        
        return False

if __name__ == "__main__":
    test_database_write()