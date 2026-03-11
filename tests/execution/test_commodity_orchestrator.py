from datetime import datetime, timedelta
from pathlib import Path

from services.commodity_strategy_orchestrator import CommodityStrategyOrchestrator, ExecutionStatus
from core.database.manager import DatabaseManager


class DummyExecutionHandler:
    def __init__(self):
        self.calls = []

    def process_signal(self, signal, current_price):
        self.calls.append((signal, current_price))
        return {"ok": True}


def _mk_orchestrator(tmp_path, exec_handler=None):
    DatabaseManager.reset_instance()
    db = DatabaseManager(Path(tmp_path))
    with db.config_writer() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS option_chain_snapshot (
                snapshot_timestamp TEXT,
                underlying_symbol TEXT,
                expiry_date TEXT,
                strike_price REAL,
                option_type TEXT,
                instrument_key TEXT,
                tradingsymbol TEXT,
                ltp REAL,
                oi INTEGER,
                iv REAL,
                delta REAL
            )
            """
        )
    return CommodityStrategyOrchestrator(db_manager=db, execution_handler=exec_handler)


def _patch_inputs(orch, now, *, stale=False, low_oi=False):
    orch._latest_underlying_price = lambda _now: (100.0, now)  # noqa: SLF001
    orch._available_expiries = lambda: [(now + timedelta(days=2)).date().isoformat(), (now + timedelta(days=9)).date().isoformat()]  # noqa: SLF001
    ts = now - timedelta(seconds=45) if stale else now
    orch._usdinr_features = lambda _now: {  # noqa: SLF001
        "timestamp": ts,
        "realized_volatility_20d": 0.22,
        "realized_volatility_5d": 0.28,
        "atr": 2.0,
    }
    oi = 100 if low_oi else 8000
    orch._option_chain_rows = lambda expiry: ([  # noqa: SLF001
        {
            "snapshot_timestamp": now,
            "strike": 100.0,
            "option_type": "CE",
            "instrument_key": "MCX_OPT|1",
            "trading_symbol": "GOLDCE100",
            "delta": 0.35,
            "open_interest": oi,
            "iv": 0.24,
            "bid": 10.0,
            "ask": 10.1,
            "underlying_ltp": 100.0,
        }
    ], now)
    orch._iv_history = lambda: [0.18, 0.2, 0.21, 0.23, 0.24]  # noqa: SLF001
    orch._slippage_samples = lambda: [2.0, 3.0]  # noqa: SLF001


def test_snapshot_id_deterministic_same_bucket(tmp_path):
    now = datetime(2026, 3, 6, 10, 0, 1)
    orch = _mk_orchestrator(tmp_path)
    _patch_inputs(orch, now)

    s1 = orch.get_latest_snapshot(now)

    orch._last_snapshot_timestamp = None  # bypass rate guard # noqa: SLF001
    s2 = orch.get_latest_snapshot(now + timedelta(seconds=2))

    assert s1.snapshot_id == s2.snapshot_id


def test_expiry_rollover_switches_to_next_if_less_than_3_days(tmp_path):
    now = datetime(2026, 3, 6, 10, 0, 0)
    orch = _mk_orchestrator(tmp_path)
    _patch_inputs(orch, now)

    snap = orch.get_latest_snapshot(now)
    assert snap.audit_meta["expiry"] == (now + timedelta(days=9)).date().isoformat()


def test_freshness_gate_rejects_snapshot(tmp_path):
    now = datetime(2026, 3, 6, 10, 0, 0)
    orch = _mk_orchestrator(tmp_path)
    _patch_inputs(orch, now, stale=True)

    snap = orch.get_latest_snapshot(now)
    assert snap.decision == "REJECT"
    assert snap.data_freshness["data_fresh"] is False
    assert snap.execution_status == ExecutionStatus.REJECTED.value


def test_liquidity_rejection_no_execution(tmp_path):
    now = datetime(2026, 3, 6, 10, 0, 0)
    exec_handler = DummyExecutionHandler()
    orch = _mk_orchestrator(tmp_path, exec_handler)
    _patch_inputs(orch, now, low_oi=True)

    snap = orch.get_latest_snapshot(now)
    assert snap.decision == "REJECT"
    assert snap.execution_status == ExecutionStatus.REJECTED.value
    assert len(exec_handler.calls) == 0


def test_accepted_snapshot_triggers_execution_intent(tmp_path):
    now = datetime(2026, 3, 6, 10, 0, 0)
    exec_handler = DummyExecutionHandler()
    orch = _mk_orchestrator(tmp_path, exec_handler)
    _patch_inputs(orch, now)

    snap = orch.get_latest_snapshot(now)
    assert snap.decision == "ACCEPT"
    assert snap.execution_status == ExecutionStatus.EXECUTED.value
    assert len(exec_handler.calls) == 1


def test_duplicate_snapshot_skipped(tmp_path):
    now = datetime(2026, 3, 6, 10, 0, 0)
    exec_handler = DummyExecutionHandler()
    orch = _mk_orchestrator(tmp_path, exec_handler)
    _patch_inputs(orch, now)

    s1 = orch.get_latest_snapshot(now)
    assert s1.execution_status == ExecutionStatus.EXECUTED.value

    orch._last_snapshot_timestamp = None  # bypass rate guard # noqa: SLF001
    s2 = orch.get_latest_snapshot(now + timedelta(seconds=3))
    assert s2.execution_status == ExecutionStatus.SKIPPED_DUPLICATE.value

