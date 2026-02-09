"""
Write Buffer - Centralized Batch Persistence
-------------------------------------------
Buffers DuckDB writes to minimize locking and improve performance.
"""
import queue
import logging
import threading
import time
from typing import List, Any
from core.database import db_cursor

logger = logging.getLogger(__name__)

class WriteBuffer:
    """
    Background worker that flushes data to DuckDB in batches.
    """
    
    def __init__(self, db_path: str = "data/trading_bot.duckdb", batch_size: int = 50, flush_interval: float = 1.0):
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._queue = queue.Queue()
        self._is_running = False
        self._thread = None

    def start(self):
        """Starts the background flush thread."""
        self._is_running = True
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stops the background thread and flushes remaining data."""
        self._is_running = False
        if self._thread:
            self._thread.join()

    def add(self, statement: str, params: List[Any]):
        """Adds a write statement to the buffer."""
        self._queue.put((statement, params))

    def _flush_loop(self):
        while self._is_running or not self._queue.empty():
            batch = []
            try:
                # Accumulate a batch
                while len(batch) < self.batch_size:
                    item = self._queue.get(timeout=self.flush_interval)
                    batch.append(item)
            except queue.Empty:
                pass

            if batch:
                self._execute_batch(batch)

    def _execute_batch(self, batch):
        try:
            with db_cursor(self.db_path) as conn:
                conn.execute("BEGIN TRANSACTION;")
                for statement, params in batch:
                    conn.execute(statement, params)
                conn.execute("COMMIT;")
        except Exception as e:
            logger.error(f"Batch write failed: {e}")
