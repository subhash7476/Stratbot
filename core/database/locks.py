import msvcrt
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class WriterLock:
    """
    Enforces single-writer rule via Windows file locking.
    Uses msvcrt.locking to provide mandatory file locking on Windows.
    """

    def __init__(self, lock_path: str, timeout: float = 5.0):
        self.lock_path = lock_path
        self.lock_file = None
        self.timeout = timeout

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire exclusive write lock. 
        If timeout > 0, will retry until successful or timeout reached.
        """
        import time
        t = timeout if timeout is not None else self.timeout
        start_time = time.time()
        
        while True:
            try:
                # Open file for writing (creates if not exists)
                if not self.lock_file:
                    self.lock_file = open(self.lock_path, 'w')
                
                # LK_NBLCK: Non-blocking lock. Raises IOError if file is already locked.
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                
                # Write current PID for debugging/health checks
                self.lock_file.seek(0)
                self.lock_file.write(f"{os.getpid()}\n")
                self.lock_file.truncate()
                self.lock_file.flush()
                
                return True
            except (IOError, OSError):
                if t <= 0 or (time.time() - start_time) >= t:
                    if self.lock_file:
                        self.lock_file.close()
                        self.lock_file = None
                    return False
                # Brief sleep before retry
                time.sleep(0.1)

    def release(self):
        """
        Release the lock.
        """
        if self.lock_file:
            try:
                # Seek to start and unlock the byte
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception as e:
                logger.error(f"Error releasing lock {self.lock_path}: {e}")
            
            self.lock_file.close()
            self.lock_file = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"Cannot acquire writer lock after {self.timeout}s: {self.lock_path}. "
                             f"Another process may be owning this database.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
