# backend/services/scanner_service.py
"""
Scanner Service
===============

Handles background scanning operations.
Runs scans in separate threads to avoid blocking the API.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import threading
import uuid
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class ScanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScanProgress:
    current: int = 0
    total: int = 0
    percent: float = 0.0
    current_symbol: Optional[str] = None
    phase: str = "initializing"


@dataclass
class ScanConfig:
    strategy: str = "squeeze_15m"
    timeframe: str = "15minute"
    lookback_days: int = 60
    sl_mode: str = "ATR based"
    atr_mult: float = 2.0
    rr_ratio: float = 2.0
    sl_pct: float = 1.0
    tp_pct: float = 2.0
    min_score: int = 4
    exclude_symbols: List[str] = field(default_factory=list)
    rebuild_resampled: bool = True
    use_live_data: bool = True


@dataclass
class ScanResult:
    symbol: str
    instrument_key: str
    signal_type: str
    entry_price: float
    sl_price: float
    tp_price: float
    score: int
    timestamp: datetime
    squeeze_count: Optional[int] = None
    momentum: Optional[float] = None
    trend_60m: Optional[str] = None
    volume_ratio: Optional[float] = None


@dataclass
class ScanJob:
    scan_id: str
    status: ScanStatus
    config: ScanConfig
    instruments: List[str]
    progress: ScanProgress
    started_at: datetime
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    tradable_signals: List[ScanResult] = field(default_factory=list)
    ready_signals: List[ScanResult] = field(default_factory=list)
    total_scanned: int = 0
    _cancel_flag: bool = False


class ScannerService:
    """
    Background scanning service.

    Runs scans in thread pool to avoid blocking API requests.
    Supports progress tracking, cancellation, and multiple concurrent scans.
    """

    _instance: Optional['ScannerService'] = None
    _instance_lock = threading.Lock()

    MAX_CONCURRENT_SCANS = 3
    MAX_HISTORY = 50  # Keep last N completed scans

    @classmethod
    def get_instance(cls) -> 'ScannerService':
        """Get singleton instance"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._scans: Dict[str, ScanJob] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=self.MAX_CONCURRENT_SCANS)
        self._callbacks: List[Callable[[str, ScanJob], None]] = []
        logger.info("ScannerService initialized")

    def add_callback(self, callback: Callable[[str, ScanJob], None]):
        """Add callback for scan updates (for WebSocket broadcasting)"""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[str, ScanJob], None]):
        """Remove callback"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_callbacks(self, scan_id: str, job: ScanJob):
        """Notify all registered callbacks"""
        for callback in self._callbacks:
            try:
                callback(scan_id, job)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def start_scan(
        self,
        config: Optional[ScanConfig] = None,
        instruments: Optional[List[str]] = None
    ) -> str:
        """
        Start a new scan in the background.

        Args:
            config: Scan configuration (uses defaults if not provided)
            instruments: List of instrument keys to scan (uses all F&O if not provided)

        Returns:
            scan_id: Unique ID for tracking the scan
        """
        with self._lock:
            # Check concurrent scan limit
            active_count = len([s for s in self._scans.values() if s.status == ScanStatus.RUNNING])
            if active_count >= self.MAX_CONCURRENT_SCANS:
                raise RuntimeError(f"Maximum concurrent scans ({self.MAX_CONCURRENT_SCANS}) reached")

            # Create scan job
            scan_id = str(uuid.uuid4())[:8]
            config = config or ScanConfig()

            # Get instruments if not provided
            if instruments is None:
                instruments = self._get_active_instruments()

            job = ScanJob(
                scan_id=scan_id,
                status=ScanStatus.PENDING,
                config=config,
                instruments=instruments,
                progress=ScanProgress(total=len(instruments)),
                started_at=datetime.now()
            )

            self._scans[scan_id] = job

            # Cleanup old scans
            self._cleanup_old_scans()

            # Start scan in thread pool
            self._executor.submit(self._run_scan, scan_id)

            logger.info(f"Scan {scan_id} started with {len(instruments)} instruments")
            return scan_id

    def get_scan(self, scan_id: str) -> Optional[ScanJob]:
        """Get scan job by ID"""
        with self._lock:
            return self._scans.get(scan_id)

    def get_scan_status(self, scan_id: str) -> Optional[Dict]:
        """Get scan status as dict"""
        job = self.get_scan(scan_id)
        if not job:
            return None

        duration = None
        if job.completed_at:
            duration = (job.completed_at - job.started_at).total_seconds()
        elif job.status == ScanStatus.RUNNING:
            duration = (datetime.now() - job.started_at).total_seconds()

        return {
            "scan_id": job.scan_id,
            "status": job.status.value,
            "progress": {
                "current": job.progress.current,
                "total": job.progress.total,
                "percent": job.progress.percent,
                "current_symbol": job.progress.current_symbol,
                "phase": job.progress.phase
            },
            "started_at": job.started_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "duration_seconds": duration,
            "error": job.error
        }

    def get_scan_results(self, scan_id: str) -> Optional[Dict]:
        """Get scan results as dict"""
        job = self.get_scan(scan_id)
        if not job:
            return None

        duration = None
        if job.completed_at:
            duration = (job.completed_at - job.started_at).total_seconds()

        return {
            "scan_id": job.scan_id,
            "status": job.status.value,
            "config": {
                "strategy": job.config.strategy,
                "timeframe": job.config.timeframe,
                "lookback_days": job.config.lookback_days,
                "sl_mode": job.config.sl_mode,
                "atr_mult": job.config.atr_mult,
                "rr_ratio": job.config.rr_ratio,
                "sl_pct": job.config.sl_pct,
                "tp_pct": job.config.tp_pct,
                "min_score": job.config.min_score
            },
            "total_scanned": job.total_scanned,
            "signals_found": len(job.tradable_signals) + len(job.ready_signals),
            "tradable_signals": [self._result_to_dict(r) for r in job.tradable_signals],
            "ready_signals": [self._result_to_dict(r) for r in job.ready_signals],
            "started_at": job.started_at.isoformat(),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "duration_seconds": duration,
            "error": job.error
        }

    def cancel_scan(self, scan_id: str) -> bool:
        """Cancel a running scan"""
        with self._lock:
            job = self._scans.get(scan_id)
            if not job:
                return False

            if job.status != ScanStatus.RUNNING:
                return False

            job._cancel_flag = True
            logger.info(f"Scan {scan_id} cancellation requested")
            return True

    def get_active_scans(self) -> List[str]:
        """Get list of active scan IDs"""
        with self._lock:
            return [
                scan_id for scan_id, job in self._scans.items()
                if job.status in (ScanStatus.PENDING, ScanStatus.RUNNING)
            ]

    def get_all_scans(self) -> List[Dict]:
        """Get summary of all scans"""
        with self._lock:
            return [
                {
                    "scan_id": job.scan_id,
                    "status": job.status.value,
                    "strategy": job.config.strategy,
                    "started_at": job.started_at.isoformat(),
                    "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                    "signals_found": len(job.tradable_signals) + len(job.ready_signals)
                }
                for job in sorted(self._scans.values(), key=lambda x: x.started_at, reverse=True)
            ]

    def cancel_all_scans(self):
        """Cancel all running scans (for shutdown)"""
        with self._lock:
            for job in self._scans.values():
                if job.status == ScanStatus.RUNNING:
                    job._cancel_flag = True

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _get_active_instruments(self) -> List[str]:
        """Get all active F&O instruments"""
        try:
            from core.live_trading_manager import LiveTradingManager
            manager = LiveTradingManager()
            instruments = manager.get_active_instruments()
            return [key for key, _ in instruments]
        except Exception as e:
            logger.error(f"Error getting instruments: {e}")
            return []

    def _run_scan(self, scan_id: str):
        """Run scan in background thread"""
        job = self._scans.get(scan_id)
        if not job:
            return

        try:
            job.status = ScanStatus.RUNNING
            job.progress.phase = "initializing"
            self._notify_callbacks(scan_id, job)

            # Get dependencies
            from core.live_trading_manager import LiveTradingManager
            from core.database import get_db

            live_manager = LiveTradingManager()
            db = get_db()

            # Phase 1: Rebuild resampled data if requested
            if job.config.rebuild_resampled:
                job.progress.phase = "resampling"
                self._notify_callbacks(scan_id, job)
                logger.info(f"Scan {scan_id}: Rebuilding resampled data...")
                live_manager.rebuild_today_resampled()

            # Check cancellation
            if job._cancel_flag:
                job.status = ScanStatus.CANCELLED
                job.completed_at = datetime.now()
                self._notify_callbacks(scan_id, job)
                return

            # Phase 2: Run the scan
            job.progress.phase = "scanning"
            self._notify_callbacks(scan_id, job)

            # Import strategy module
            from core.strategies.indian_market_squeeze import build_15m_signals_with_backtest

            tradable = []
            ready = []

            for i, instrument_key in enumerate(job.instruments):
                # Check cancellation
                if job._cancel_flag:
                    job.status = ScanStatus.CANCELLED
                    job.completed_at = datetime.now()
                    self._notify_callbacks(scan_id, job)
                    return

                # Update progress
                job.progress.current = i + 1
                job.progress.percent = (i + 1) / len(job.instruments) * 100

                # Get symbol name
                try:
                    symbol_row = db.con.execute(
                        "SELECT trading_symbol FROM fo_stocks_master WHERE instrument_key = ?",
                        [instrument_key]
                    ).fetchone()
                    symbol = symbol_row[0] if symbol_row else instrument_key.split("|")[-1]
                except:
                    symbol = instrument_key.split("|")[-1]

                job.progress.current_symbol = symbol

                # Notify every 5 instruments or on significant progress
                if i % 5 == 0:
                    self._notify_callbacks(scan_id, job)

                # Skip excluded symbols
                if symbol in job.config.exclude_symbols:
                    continue

                try:
                    # Get MTF data
                    df_60m, df_15m, df_5m = live_manager.get_live_mtf_data(
                        instrument_key,
                        lookback_days=job.config.lookback_days
                    )

                    if df_15m is None or len(df_15m) < 50:
                        continue

                    # Run strategy
                    signals_df, _ = build_15m_signals_with_backtest(
                        df_15m=df_15m,
                        df_60m=df_60m,
                        instrument_key=instrument_key,
                        lookback_bars=200
                    )

                    if signals_df is None or signals_df.empty:
                        continue

                    # Get latest signal
                    latest = signals_df.iloc[-1]

                    # Check if it's an active signal
                    if latest.get('signal', 0) == 0:
                        continue

                    score = int(latest.get('score', 0))
                    if score < job.config.min_score:
                        continue

                    # Calculate SL/TP
                    entry_price = float(latest['close'])

                    if job.config.sl_mode == "ATR based":
                        atr = float(latest.get('atr', entry_price * 0.02))
                        sl_distance = atr * job.config.atr_mult
                        tp_distance = sl_distance * job.config.rr_ratio
                    else:
                        sl_distance = entry_price * (job.config.sl_pct / 100)
                        tp_distance = entry_price * (job.config.tp_pct / 100)

                    signal_type = "LONG" if latest['signal'] > 0 else "SHORT"

                    if signal_type == "LONG":
                        sl_price = entry_price - sl_distance
                        tp_price = entry_price + tp_distance
                    else:
                        sl_price = entry_price + sl_distance
                        tp_price = entry_price - tp_distance

                    result = ScanResult(
                        symbol=symbol,
                        instrument_key=instrument_key,
                        signal_type=signal_type,
                        entry_price=entry_price,
                        sl_price=round(sl_price, 2),
                        tp_price=round(tp_price, 2),
                        score=score,
                        timestamp=datetime.now(),
                        squeeze_count=int(latest.get('squeeze_count', 0)),
                        momentum=float(latest.get('momentum', 0)),
                        trend_60m=str(latest.get('trend_60m', 'N/A')),
                        volume_ratio=float(latest.get('volume_ratio', 1.0)) if 'volume_ratio' in latest else None
                    )

                    if score == 5:
                        tradable.append(result)
                    else:
                        ready.append(result)

                    job.total_scanned += 1

                except Exception as e:
                    logger.warning(f"Error scanning {symbol}: {e}")
                    continue

            # Phase 3: Finalize
            job.progress.phase = "finalizing"
            job.progress.current = len(job.instruments)
            job.progress.percent = 100.0
            job.progress.current_symbol = None

            job.tradable_signals = tradable
            job.ready_signals = ready
            job.status = ScanStatus.COMPLETED
            job.completed_at = datetime.now()

            logger.info(
                f"Scan {scan_id} completed: {len(tradable)} tradable, {len(ready)} ready signals"
            )

            self._notify_callbacks(scan_id, job)

        except Exception as e:
            logger.error(f"Scan {scan_id} failed: {e}", exc_info=True)
            job.status = ScanStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now()
            self._notify_callbacks(scan_id, job)

    def _result_to_dict(self, result: ScanResult) -> Dict:
        """Convert ScanResult to dict"""
        return {
            "symbol": result.symbol,
            "instrument_key": result.instrument_key,
            "signal_type": result.signal_type,
            "entry_price": result.entry_price,
            "sl_price": result.sl_price,
            "tp_price": result.tp_price,
            "score": result.score,
            "timestamp": result.timestamp.isoformat(),
            "squeeze_count": result.squeeze_count,
            "momentum": result.momentum,
            "trend_60m": result.trend_60m,
            "volume_ratio": result.volume_ratio
        }

    def _cleanup_old_scans(self):
        """Remove old completed scans to prevent memory growth"""
        with self._lock:
            completed = [
                (scan_id, job) for scan_id, job in self._scans.items()
                if job.status in (ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED)
            ]

            # Sort by completion time
            completed.sort(key=lambda x: x[1].completed_at or x[1].started_at)

            # Remove oldest if over limit
            while len(completed) > self.MAX_HISTORY:
                old_id, _ = completed.pop(0)
                del self._scans[old_id]
                logger.debug(f"Cleaned up old scan {old_id}")
