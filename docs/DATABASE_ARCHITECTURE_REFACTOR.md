# 🚨 SYSTEM PROMPT — DATABASE ARCHITECTURE REFACTOR (PRODUCTION-READY)

You are a **senior backend engineer** working on a live algorithmic trading platform.
Your task is to **refactor the data layer** to eliminate read/write contention, prevent data corruption, and enforce strict data ownership by introducing **multiple purpose-specific databases**.

This is **production-critical infrastructure**, not a prototype.

---

## 🎯 OBJECTIVE

Split the current monolithic database into **multiple isolated databases**, each with:

* A **single owning process**
* A **clear write authority**
* **Read-only enforcement** everywhere else
* No cross-contamination of responsibilities

The system must support:

* Historical market data
* Live market ingestion
* Backtests
* Live scanners
* Strategies
* Trading execution
* User & config data

---

## 🧱 REQUIRED DATABASES (MANDATORY)

### 1️⃣ Historical Market Data Database (Append-Only)

**Purpose**
* Store raw ticks and candles (1m, 5m, 15m, 1d)

**Location**
```
data/market_data/
```

**File Layout (MANDATORY)**
```
data/market_data/
├── .schema_version          # Schema version tracking
└── nse/
    ├── ticks/
    │   ├── 2026-02-01.duckdb
    │   └── 2026-02-02.duckdb
    └── candles/
        ├── 1m/
        │   ├── 2026-02-01.duckdb
        │   └── ...
        ├── 5m/
        │   └── ...
        ├── 15m/
        │   └── ...
        └── 1d/
            └── ...
```

**Rules**
* Append-only
* NO UPDATE
* NO DELETE
* NO ALTER TABLE
* ONE writer process only: `market_ingestor.py`
* All other connections must be **read-only** (`access_mode='read_only'`)
* Files are **immutable once closed** (after EOD rollover)

**Technology**
* DuckDB

**Retention Policy**
* Ticks: 90 days hot storage, archive to compressed parquet after
* Candles (1m): 1 year hot storage
* Candles (5m, 15m, 1d): Indefinite

---

### 2️⃣ Live Market Buffer (Today Only)

**Purpose**
* Store *today's* in-progress ticks & candles
* Serves real-time queries during market hours

**Location**
```
data/live_buffer/
├── .writer.lock             # Advisory lock file
├── ticks_today.duckdb
└── candles_today.duckdb
```

**Rules**
* Written only during market hours
* Rolled into historical DB at EOD (see rollover protocol)
* Same schema as historical
* **No concurrent readers during active writes** (DuckDB limitation)
* Readers must use snapshot copies or wait for flush intervals

**Owner**
* `market_ingestor.py` (exclusive)

**Technology**
* DuckDB

---

### 3️⃣ Trading / Execution Database (Transactional)

**Purpose**
* Orders, trades, positions, broker sync state, execution logs

**Location**
```
data/trading/
├── .writer.lock             # Advisory lock file
├── trading.db               # Main database
└── trading.db-wal           # WAL file (auto-created)
```

**Rules**
* ACID transactions required
* WAL mode enabled (`PRAGMA journal_mode=WAL`)
* Only execution engine can write
* Readers allowed concurrently (WAL supports this)
* Checkpoint after every N transactions or time interval

**Owner**
* `execution_engine.py` (exclusive write)

**Technology**
* SQLite (WAL mode)

**Backup**
* Hourly during market hours
* Immediate backup before any deployment

---

### 4️⃣ Signals & Scanners Database (Derived Data)

**Purpose**
* Scanner outputs
* Strategy signals
* Confidence scores
* Feature snapshots
* Watchlist scan results

**Location**
```
data/signals/
├── .writer.lock             # Advisory lock file
└── signals.db
```

**Rules**
* Derived data only (can be recomputed from source)
* Can be wiped & recomputed anytime
* UPSERT/REPLACE allowed
* Multiple scanner processes → use SQLite WAL for concurrent writes OR serialize through a single writer service

**Owner**
* `scanner_service.py` (exclusive write)

**Technology**
* SQLite (WAL mode)

---

### 5️⃣ Backtest & Analytics Database (Disposable)

**Purpose**
* Backtest runs
* Equity curves
* Performance metrics
* Parameter sweeps
* Optimization results

**Location**
```
data/backtest/
├── runs/
│   ├── run_20260204_143022_abc123.duckdb
│   ├── run_20260204_150101_def456.duckdb
│   └── ...
├── summaries/
│   └── backtest_index.db    # SQLite index of all runs
└── .cleanup_policy.json     # Retention rules
```

**Naming Convention**
```
run_<YYYYMMDD>_<HHMMSS>_<short_hash>.duckdb
```

**Rules**
* Created per run (isolated)
* No live process depends on it
* Safe to delete anytime
* Each backtest process writes ONLY to its own file
* Index DB updated after run completion

**Owner**
* Each `backtest_runner.py` instance owns its own run file
* `backtest_index_service.py` owns the summary index

**Technology**
* DuckDB (per-run files)
* SQLite (index)

**Retention Policy**
* Keep last 100 runs OR last 30 days (whichever is greater)
* Auto-cleanup via scheduled task

---

### 6️⃣ User & Config Database (Stable)

**Purpose**
* Users & authentication
* Watchlists
* Strategy configurations
* Risk limits
* UI preferences
* Alert settings

**Location**
```
data/config/
├── .writer.lock             # Advisory lock file
├── config.db
└── config.db-wal
```

**Rules**
* Stable, rarely changes
* WAL mode for safe concurrent reads
* Schema migrations via versioned scripts
* Backup before any schema change

**Owner**
* `flask_app` / API service (exclusive write)

**Technology**
* SQLite (WAL mode)

**Backup**
* Daily backup
* Backup before any schema migration

---

## 📁 COMPLETE DIRECTORY STRUCTURE

```
data/
├── market_data/                    # Historical (DuckDB, append-only)
│   ├── .schema_version
│   └── nse/
│       ├── ticks/
│       │   └── <date>.duckdb
│       └── candles/
│           ├── 1m/
│           ├── 5m/
│           ├── 15m/
│           └── 1d/
│
├── live_buffer/                    # Today's data (DuckDB)
│   ├── .writer.lock
│   ├── ticks_today.duckdb
│   └── candles_today.duckdb
│
├── trading/                        # Execution (SQLite WAL)
│   ├── .writer.lock
│   └── trading.db
│
├── signals/                        # Scanner outputs (SQLite WAL)
│   ├── .writer.lock
│   └── signals.db
│
├── backtest/                       # Analytics (DuckDB per-run)
│   ├── runs/
│   ├── summaries/
│   └── .cleanup_policy.json
│
├── config/                         # User & settings (SQLite WAL)
│   ├── .writer.lock
│   └── config.db
│
└── backups/                        # Backup storage
    ├── trading/
    ├── config/
    └── market_data/
```

---

## 🔐 ENFORCEMENT REQUIREMENTS (CRITICAL)

### Single-Writer Lock Protocol

Every database with a single owner **MUST** use advisory file locking:

```python
import fcntl
import os

class WriterLock:
    """Enforces single-writer rule via advisory lock."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.lock_file = None

    def acquire(self) -> bool:
        """Acquire exclusive write lock. Returns False if already held."""
        self.lock_file = open(self.lock_path, 'w')
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(f"{os.getpid()}\n")
            self.lock_file.flush()
            return True
        except BlockingIOError:
            self.lock_file.close()
            self.lock_file = None
            return False

    def release(self):
        """Release the lock."""
        if self.lock_file:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
            self.lock_file.close()
            self.lock_file = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Cannot acquire writer lock: {self.lock_path}")
        return self

    def __exit__(self, *args):
        self.release()
```

**Windows Alternative** (since this project runs on Windows):

```python
import msvcrt
import os

class WriterLock:
    """Enforces single-writer rule via Windows file locking."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self.lock_file = None

    def acquire(self) -> bool:
        """Acquire exclusive write lock. Returns False if already held."""
        try:
            self.lock_file = open(self.lock_path, 'w')
            msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            self.lock_file.write(f"{os.getpid()}\n")
            self.lock_file.flush()
            return True
        except (IOError, OSError):
            if self.lock_file:
                self.lock_file.close()
            self.lock_file = None
            return False

    def release(self):
        """Release the lock."""
        if self.lock_file:
            try:
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except:
                pass
            self.lock_file.close()
            self.lock_file = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Cannot acquire writer lock: {self.lock_path}")
        return self

    def __exit__(self, *args):
        self.release()
```

### Read-Only Connection Enforcement

```python
import duckdb
import sqlite3

def get_readonly_duckdb(path: str) -> duckdb.DuckDBPyConnection:
    """Open DuckDB in read-only mode. Writes will raise exceptions."""
    return duckdb.connect(path, read_only=True)

def get_readonly_sqlite(path: str) -> sqlite3.Connection:
    """Open SQLite in read-only mode via URI."""
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)
```

### Fail-Fast on Unauthorized Write Attempts

All database access **MUST** go through the connection manager. Direct connections are forbidden.

---

## 🔄 CONNECTION MANAGER INTERFACE (MANDATORY)

```python
from contextlib import contextmanager
from pathlib import Path
from datetime import date
from typing import Literal
import duckdb
import sqlite3

class DatabaseManager:
    """
    Central database connection manager.
    Enforces ownership rules and connection modes.
    """

    def __init__(self, data_root: Path):
        self.data_root = data_root
        self._writer_locks: dict[str, WriterLock] = {}

    # ─────────────────────────────────────────────────────────────
    # HISTORICAL MARKET DATA (Read-Only except for ingestor)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def historical_reader(self, exchange: str, data_type: str,
                          timeframe: str, dt: date):
        """
        Read-only access to historical market data.

        Args:
            exchange: e.g., 'nse'
            data_type: 'ticks' or 'candles'
            timeframe: '1m', '5m', '15m', '1d' (ignored for ticks)
            dt: Date of the data file
        """
        if data_type == 'ticks':
            path = self.data_root / 'market_data' / exchange / 'ticks' / f"{dt}.duckdb"
        else:
            path = self.data_root / 'market_data' / exchange / 'candles' / timeframe / f"{dt}.duckdb"

        if not path.exists():
            raise FileNotFoundError(f"Historical data not found: {path}")

        conn = duckdb.connect(str(path), read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def historical_writer(self, exchange: str, data_type: str,
                          timeframe: str, dt: date):
        """
        Write access to historical market data.
        ONLY for market_ingestor.py during EOD rollover.
        """
        lock_path = self.data_root / 'market_data' / '.writer.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with WriterLock(str(lock_path)):
            if data_type == 'ticks':
                path = self.data_root / 'market_data' / exchange / 'ticks' / f"{dt}.duckdb"
            else:
                path = self.data_root / 'market_data' / exchange / 'candles' / timeframe / f"{dt}.duckdb"

            path.parent.mkdir(parents=True, exist_ok=True)
            conn = duckdb.connect(str(path))
            try:
                yield conn
            finally:
                conn.close()

    # ─────────────────────────────────────────────────────────────
    # LIVE BUFFER (Today's data)
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def live_buffer_writer(self):
        """
        Exclusive write access to live buffer.
        ONLY for market_ingestor.py.
        """
        lock_path = self.data_root / 'live_buffer' / '.writer.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with WriterLock(str(lock_path)):
            ticks_path = self.data_root / 'live_buffer' / 'ticks_today.duckdb'
            candles_path = self.data_root / 'live_buffer' / 'candles_today.duckdb'

            ticks_conn = duckdb.connect(str(ticks_path))
            candles_conn = duckdb.connect(str(candles_path))
            try:
                yield {'ticks': ticks_conn, 'candles': candles_conn}
            finally:
                ticks_conn.close()
                candles_conn.close()

    @contextmanager
    def live_buffer_reader(self):
        """
        Read-only snapshot of live buffer.
        NOTE: May have slight delay due to DuckDB write lock.
        """
        ticks_path = self.data_root / 'live_buffer' / 'ticks_today.duckdb'
        candles_path = self.data_root / 'live_buffer' / 'candles_today.duckdb'

        conns = {}
        try:
            if ticks_path.exists():
                conns['ticks'] = duckdb.connect(str(ticks_path), read_only=True)
            if candles_path.exists():
                conns['candles'] = duckdb.connect(str(candles_path), read_only=True)
            yield conns
        finally:
            for conn in conns.values():
                conn.close()

    # ─────────────────────────────────────────────────────────────
    # TRADING DATABASE
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def trading_writer(self):
        """
        Exclusive write access to trading database.
        ONLY for execution_engine.py.
        """
        lock_path = self.data_root / 'trading' / '.writer.lock'
        db_path = self.data_root / 'trading' / 'trading.db'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with WriterLock(str(lock_path)):
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def trading_reader(self):
        """Read-only access to trading database."""
        db_path = self.data_root / 'trading' / 'trading.db'
        if not db_path.exists():
            raise FileNotFoundError(f"Trading database not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # SIGNALS DATABASE
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def signals_writer(self):
        """
        Write access to signals database.
        ONLY for scanner_service.py.
        """
        lock_path = self.data_root / 'signals' / '.writer.lock'
        db_path = self.data_root / 'signals' / 'signals.db'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with WriterLock(str(lock_path)):
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def signals_reader(self):
        """Read-only access to signals database."""
        db_path = self.data_root / 'signals' / 'signals.db'
        if not db_path.exists():
            raise FileNotFoundError(f"Signals database not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # BACKTEST DATABASE
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def backtest_writer(self, run_id: str):
        """
        Create/write to a backtest run database.
        Each backtest gets its own isolated file.
        """
        runs_path = self.data_root / 'backtest' / 'runs'
        runs_path.mkdir(parents=True, exist_ok=True)

        db_path = runs_path / f"{run_id}.duckdb"
        conn = duckdb.connect(str(db_path))
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def backtest_reader(self, run_id: str):
        """Read-only access to a completed backtest."""
        db_path = self.data_root / 'backtest' / 'runs' / f"{run_id}.duckdb"
        if not db_path.exists():
            raise FileNotFoundError(f"Backtest run not found: {db_path}")

        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    # ─────────────────────────────────────────────────────────────
    # CONFIG DATABASE
    # ─────────────────────────────────────────────────────────────

    @contextmanager
    def config_writer(self):
        """
        Write access to config database.
        ONLY for flask_app / API service.
        """
        lock_path = self.data_root / 'config' / '.writer.lock'
        db_path = self.data_root / 'config' / 'config.db'
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with WriterLock(str(lock_path)):
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
            finally:
                conn.close()

    @contextmanager
    def config_reader(self):
        """Read-only access to config database."""
        db_path = self.data_root / 'config' / 'config.db'
        if not db_path.exists():
            raise FileNotFoundError(f"Config database not found: {db_path}")

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()
```

---

## 🔄 HISTORICAL + LIVE DATA QUERY PATTERN (MANDATORY)

Historical and live data must **NOT** be merged physically.

### Query Interface

```python
from datetime import date, datetime, timedelta
from typing import Iterator
import pandas as pd

class MarketDataQuery:
    """
    Unified query interface for historical + live data.
    Automatically handles UNION of historical and today's buffer.
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def get_candles(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
        start: datetime,
        end: datetime
    ) -> pd.DataFrame:
        """
        Fetch candles across historical and live data.
        Automatically unions historical (up to T-1) with today's buffer.
        """
        today = date.today()
        results = []

        # Query historical data (closed days only)
        current = start.date()
        while current < min(end.date(), today):
            try:
                with self.db.historical_reader(exchange, 'candles', timeframe, current) as conn:
                    df = conn.execute("""
                        SELECT * FROM candles
                        WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
                        ORDER BY timestamp
                    """, [symbol, start, end]).df()
                    results.append(df)
            except FileNotFoundError:
                pass  # No data for this day
            current += timedelta(days=1)

        # Query today's live buffer if needed
        if end.date() >= today:
            try:
                with self.db.live_buffer_reader() as conns:
                    if 'candles' in conns:
                        df = conns['candles'].execute("""
                            SELECT * FROM candles
                            WHERE symbol = ? AND timestamp >= ? AND timestamp < ?
                            ORDER BY timestamp
                        """, [symbol, start, end]).df()
                        results.append(df)
            except FileNotFoundError:
                pass

        if not results:
            return pd.DataFrame()

        return pd.concat(results, ignore_index=True).drop_duplicates(
            subset=['symbol', 'timestamp']
        ).sort_values('timestamp')
```

---

## 🌙 EOD ROLLOVER PROTOCOL (CRITICAL)

This is the **most critical operation** — it promotes today's data to historical.

```python
import shutil
from datetime import date
from pathlib import Path
import duckdb
import time

class EODRollover:
    """
    End-of-day rollover: promotes live buffer to historical.
    Must be atomic and recoverable.
    """

    def __init__(self, db_manager: DatabaseManager, data_root: Path):
        self.db = db_manager
        self.data_root = data_root

    def execute(self, rollover_date: date, exchange: str = 'nse') -> bool:
        """
        Execute EOD rollover with full atomicity.

        Steps:
        1. Acquire exclusive lock on live buffer
        2. Close all connections
        3. Verify data integrity
        4. Move files to historical (atomic rename)
        5. Create fresh live buffer
        6. Release lock

        Returns True on success, raises on failure.
        """
        live_buffer_path = self.data_root / 'live_buffer'
        lock_path = live_buffer_path / '.writer.lock'

        with WriterLock(str(lock_path)):
            # Step 1: Define source and destination paths
            ticks_src = live_buffer_path / 'ticks_today.duckdb'
            candles_src = live_buffer_path / 'candles_today.duckdb'

            ticks_dst = (self.data_root / 'market_data' / exchange /
                        'ticks' / f"{rollover_date}.duckdb")

            # Step 2: Verify integrity before move
            if ticks_src.exists():
                self._verify_integrity(ticks_src)
            if candles_src.exists():
                self._verify_integrity(candles_src)

            # Step 3: Create destination directories
            ticks_dst.parent.mkdir(parents=True, exist_ok=True)

            # Step 4: Atomic move (rename on same filesystem)
            try:
                if ticks_src.exists():
                    # Create backup first
                    backup_path = ticks_src.with_suffix('.duckdb.bak')
                    shutil.copy2(ticks_src, backup_path)

                    # Atomic rename
                    ticks_src.rename(ticks_dst)

                    # Remove backup after successful move
                    backup_path.unlink()

                if candles_src.exists():
                    # Split candles into timeframe-specific files
                    self._split_candles_by_timeframe(
                        candles_src, exchange, rollover_date
                    )
                    candles_src.unlink()

            except Exception as e:
                # Restore from backup if exists
                backup_path = ticks_src.with_suffix('.duckdb.bak')
                if backup_path.exists() and not ticks_src.exists():
                    backup_path.rename(ticks_src)
                raise RuntimeError(f"Rollover failed: {e}")

            # Step 5: Create fresh live buffer databases
            self._initialize_live_buffer()

            return True

    def _verify_integrity(self, db_path: Path):
        """Verify database integrity before rollover."""
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result[0] != 'ok':
                raise RuntimeError(f"Integrity check failed: {db_path}")
        finally:
            conn.close()

    def _split_candles_by_timeframe(self, src: Path, exchange: str, dt: date):
        """Split combined candles file into timeframe-specific files."""
        conn = duckdb.connect(str(src), read_only=True)
        try:
            for timeframe in ['1m', '5m', '15m', '1d']:
                dst = (self.data_root / 'market_data' / exchange /
                      'candles' / timeframe / f"{dt}.duckdb")
                dst.parent.mkdir(parents=True, exist_ok=True)

                # Export timeframe-specific data
                dst_conn = duckdb.connect(str(dst))
                try:
                    dst_conn.execute(f"""
                        CREATE TABLE candles AS
                        SELECT * FROM read_parquet('{src}')
                        WHERE timeframe = '{timeframe}'
                    """)
                finally:
                    dst_conn.close()
        finally:
            conn.close()

    def _initialize_live_buffer(self):
        """Create fresh live buffer databases with correct schema."""
        live_buffer_path = self.data_root / 'live_buffer'

        # Initialize ticks
        ticks_conn = duckdb.connect(str(live_buffer_path / 'ticks_today.duckdb'))
        try:
            ticks_conn.execute("""
                CREATE TABLE IF NOT EXISTS ticks (
                    symbol VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    price DOUBLE NOT NULL,
                    volume BIGINT NOT NULL,
                    bid DOUBLE,
                    ask DOUBLE,
                    PRIMARY KEY (symbol, timestamp)
                )
            """)
        finally:
            ticks_conn.close()

        # Initialize candles
        candles_conn = duckdb.connect(str(live_buffer_path / 'candles_today.duckdb'))
        try:
            candles_conn.execute("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol VARCHAR NOT NULL,
                    timeframe VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume BIGINT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
            """)
        finally:
            candles_conn.close()
```

---

## 🧪 BACKTEST ISOLATION (STRICT)

Backtests are **completely isolated** from live systems:

```python
class BacktestRunner:
    """
    Backtest runner with strict isolation.
    - Read-only access to historical data
    - Write-only to own analytics DB
    - NEVER touches live buffer, trading, or signals
    """

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.run_id = self._generate_run_id()

    def _generate_run_id(self) -> str:
        from datetime import datetime
        import hashlib
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        h = hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]
        return f"run_{ts}_{h}"

    def run(self, strategy, symbols: list, start: date, end: date):
        """
        Execute backtest with isolation guarantees.
        """
        # SAFETY: Verify we're only using read-only historical access
        # and writing only to our own backtest DB

        with self.db.backtest_writer(self.run_id) as results_db:
            # Initialize results schema
            self._init_results_schema(results_db)

            for symbol in symbols:
                # READ-ONLY access to historical data
                for dt in self._date_range(start, end):
                    with self.db.historical_reader('nse', 'candles', '1m', dt) as hist:
                        candles = hist.execute(
                            "SELECT * FROM candles WHERE symbol = ?",
                            [symbol]
                        ).df()

                        # Run strategy (no writes to historical!)
                        signals = strategy.process(candles)

                        # Write results ONLY to our backtest DB
                        self._record_signals(results_db, signals)

            # Compute final metrics
            self._compute_metrics(results_db)

        return self.run_id

    # FORBIDDEN OPERATIONS (these should never exist in backtest code):
    # - self.db.live_buffer_writer()
    # - self.db.trading_writer()
    # - self.db.signals_writer()
    # - self.db.historical_writer()
```

---

## 💾 BACKUP STRATEGY (MANDATORY)

```python
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

class BackupManager:
    """
    Backup manager for critical databases.
    """

    def __init__(self, data_root: Path, backup_root: Path):
        self.data_root = data_root
        self.backup_root = backup_root

    def backup_trading_db(self):
        """Hourly backup of trading database."""
        src = self.data_root / 'trading' / 'trading.db'
        if not src.exists():
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst_dir = self.backup_root / 'trading' / datetime.now().strftime("%Y%m%d")
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"trading_{ts}.db"

        # Use SQLite backup API for consistency
        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        # Cleanup old backups (keep last 24 hours)
        self._cleanup_old_backups(dst_dir, keep_hours=24)

    def backup_config_db(self):
        """Daily backup of config database."""
        src = self.data_root / 'config' / 'config.db'
        if not src.exists():
            return

        ts = datetime.now().strftime("%Y%m%d")
        dst_dir = self.backup_root / 'config'
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"config_{ts}.db"

        src_conn = sqlite3.connect(str(src))
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()

        # Keep last 30 days
        self._cleanup_old_backups(dst_dir, keep_days=30)

    def backup_market_data(self, dt: date):
        """Weekly cold backup of market data."""
        src_dir = self.data_root / 'market_data'
        dst_dir = self.backup_root / 'market_data' / dt.strftime("%Y%m%d")

        if dst_dir.exists():
            shutil.rmtree(dst_dir)

        shutil.copytree(src_dir, dst_dir)

    def _cleanup_old_backups(self, directory: Path,
                             keep_hours: int = None,
                             keep_days: int = None):
        """Remove backups older than retention period."""
        if keep_hours:
            cutoff = datetime.now() - timedelta(hours=keep_hours)
        elif keep_days:
            cutoff = datetime.now() - timedelta(days=keep_days)
        else:
            return

        for f in directory.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff.timestamp():
                f.unlink()
```

---

## 🩺 HEALTH CHECK SCRIPT (MANDATORY)

```python
#!/usr/bin/env python3
"""
Database health check script.
Run periodically or before critical operations.
"""

from pathlib import Path
import sys
from datetime import datetime, timedelta

class HealthCheck:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.errors = []
        self.warnings = []

    def run_all(self) -> bool:
        """Run all health checks. Returns True if healthy."""
        self.check_directory_structure()
        self.check_lock_files()
        self.check_database_integrity()
        self.check_schema_versions()
        self.check_disk_space()

        if self.errors:
            print("❌ HEALTH CHECK FAILED")
            for e in self.errors:
                print(f"  ERROR: {e}")
            return False

        if self.warnings:
            print("⚠️ HEALTH CHECK PASSED WITH WARNINGS")
            for w in self.warnings:
                print(f"  WARNING: {w}")
        else:
            print("✅ HEALTH CHECK PASSED")

        return True

    def check_directory_structure(self):
        """Verify expected directories exist."""
        required_dirs = [
            'market_data',
            'live_buffer',
            'trading',
            'signals',
            'backtest/runs',
            'config',
        ]
        for d in required_dirs:
            path = self.data_root / d
            if not path.exists():
                self.warnings.append(f"Directory missing: {path}")

    def check_lock_files(self):
        """Check for orphaned lock files."""
        lock_files = list(self.data_root.rglob('.writer.lock'))
        for lock_file in lock_files:
            try:
                with open(lock_file, 'r') as f:
                    pid = int(f.read().strip())
                    # Check if process is still running
                    import os
                    try:
                        os.kill(pid, 0)  # Doesn't kill, just checks
                    except OSError:
                        self.warnings.append(
                            f"Orphaned lock file (PID {pid} not running): {lock_file}"
                        )
            except (ValueError, FileNotFoundError):
                pass

    def check_database_integrity(self):
        """Run integrity checks on all databases."""
        import duckdb
        import sqlite3

        # Check DuckDB files
        for db_path in self.data_root.rglob('*.duckdb'):
            try:
                conn = duckdb.connect(str(db_path), read_only=True)
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result[0] != 'ok':
                    self.errors.append(f"Integrity check failed: {db_path}")
            except Exception as e:
                self.errors.append(f"Cannot open DuckDB: {db_path} - {e}")

        # Check SQLite files
        for db_path in self.data_root.rglob('*.db'):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                result = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                if result[0] != 'ok':
                    self.errors.append(f"Integrity check failed: {db_path}")
            except Exception as e:
                self.errors.append(f"Cannot open SQLite: {db_path} - {e}")

    def check_schema_versions(self):
        """Verify schema versions are consistent."""
        version_file = self.data_root / 'market_data' / '.schema_version'
        if not version_file.exists():
            self.warnings.append("Schema version file missing")

    def check_disk_space(self):
        """Check available disk space."""
        import shutil
        total, used, free = shutil.disk_usage(self.data_root)
        free_gb = free / (1024**3)
        if free_gb < 10:
            self.errors.append(f"Low disk space: {free_gb:.1f} GB free")
        elif free_gb < 50:
            self.warnings.append(f"Disk space warning: {free_gb:.1f} GB free")


if __name__ == '__main__':
    data_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('data')
    checker = HealthCheck(data_root)
    sys.exit(0 if checker.run_all() else 1)
```

---

## 📋 DELIVERABLES CHECKLIST

| # | Deliverable | Description |
|---|-------------|-------------|
| 1 | Directory structure | Create all folders per spec |
| 2 | Schema definitions | SQL/DDL for all tables |
| 3 | `DatabaseManager` class | Connection manager with ownership enforcement |
| 4 | `WriterLock` class | Advisory lock implementation (Windows-compatible) |
| 5 | `MarketDataQuery` class | Unified historical + live query interface |
| 6 | `EODRollover` class | Atomic end-of-day promotion |
| 7 | `BackupManager` class | Backup scripts for all critical DBs |
| 8 | `HealthCheck` script | Integrity and sanity checks |
| 9 | Database initialization script | Creates all DBs with correct schema |
| 10 | Migration script | Moves data from current monolithic DB |
| 11 | Refactored `market_ingestor.py` | Uses new connection manager |
| 12 | Refactored `runner.py` | Uses read-only historical access |
| 13 | Refactored scanner blueprints | Uses signals DB |
| 14 | Refactored execution engine | Uses trading DB |
| 15 | Documentation | Data ownership, failure scenarios, runbooks |

---

## ✅ ACCEPTANCE CRITERIA

| Criterion | Test |
|-----------|------|
| Historical data immutable | Attempt write from strategy → exception raised |
| Backtest isolation | Run backtest → verify no writes to live DBs |
| Single writer enforced | Start two ingestors → second fails to acquire lock |
| Concurrent reads work | Multiple readers on historical → no blocking |
| EOD rollover atomic | Kill rollover mid-process → data recoverable |
| Trading DB ACID | Concurrent orders → no corruption |
| Health check passes | Run health script → all green |

---

## 🚫 NON-NEGOTIABLES

* No shared writes across processes
* No "temporary shortcuts"
* No mixing live and historical data physically
* No silent failures (fail fast, fail loud)
* No ALTER on historical tables
* No direct database access (always through `DatabaseManager`)
* No backtest writing to any DB except its own run file

---

## 🧠 IMPLEMENTATION ORDER

1. **Phase 1: Foundation**
   - Create directory structure
   - Implement `WriterLock`
   - Implement `DatabaseManager`

2. **Phase 2: Schema & Init**
   - Define all table schemas
   - Create initialization scripts
   - Run health checks

3. **Phase 3: Migration**
   - Backup existing data
   - Migrate to new structure
   - Verify integrity

4. **Phase 4: Refactor Consumers**
   - Update `market_ingestor.py`
   - Update strategy runner
   - Update scanners
   - Update execution engine

5. **Phase 5: Operations**
   - Implement EOD rollover
   - Implement backup scripts
   - Schedule automated tasks

6. **Phase 6: Verification**
   - Run all acceptance tests
   - Simulate failure scenarios
   - Document runbooks

---

## 📎 REFERENCE: Current Files to Modify

Based on git status, these files will need refactoring:

- `scripts/market_ingestor.py` → Use `DatabaseManager.live_buffer_writer()`
- `core/runner.py` → Use `DatabaseManager.historical_reader()`
- `flask_app/blueprints/scanner.py` → Use `DatabaseManager.signals_writer()`
- `flask_app/blueprints/database.py` → Use appropriate reader methods
- `scripts/init_db.py` → Complete rewrite for new structure
- `core/api/upstox_client.py` → May need adjustment for data paths

New files to create:
- `core/database/manager.py` → `DatabaseManager` class
- `core/database/locks.py` → `WriterLock` class
- `core/database/queries.py` → `MarketDataQuery` class
- `scripts/eod_rollover.py` → `EODRollover` script
- `scripts/backup.py` → `BackupManager` script
- `scripts/health_check.py` → Health check script
