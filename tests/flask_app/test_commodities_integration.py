from datetime import datetime
from pathlib import Path

from flask import Flask

from core.database.manager import DatabaseManager
from flask_app.blueprints.commodities import commodities_bp


class DummyExecutionHandler:
    def process_signal(self, signal, current_price):
        return {"ok": True}


def _build_app(tmp_path):
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
        now = datetime(2026, 3, 6, 10, 0, 0)
        conn.execute(
            """
            INSERT INTO option_chain_snapshot
            (snapshot_timestamp, underlying_symbol, expiry_date, strike_price, option_type, instrument_key, tradingsymbol, ltp, oi, iv, delta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                now.isoformat(),
                "MCX|GOLD",
                "2026-03-20",
                100.0,
                "CE",
                "MCX_OPT|1",
                "GOLDCE100",
                10.0,
                12000,
                0.22,
                0.35,
            ],
        )

    app = Flask(__name__)
    app.db_manager = db
    app.execution_handler = DummyExecutionHandler()
    app.register_blueprint(commodities_bp)
    return app


def test_state_api_contains_strategy_snapshot_and_audit_meta(tmp_path):
    app = _build_app(tmp_path)
    client = app.test_client()

    res = client.get("/commodities/api/state")
    assert res.status_code == 200
    payload = res.get_json()

    assert "strategy_snapshot" in payload
    assert "audit_meta" in payload

    snap = payload["strategy_snapshot"]
    expected_keys = [
        "snapshot_id",
        "regime",
        "strike_selection",
        "liquidity_check",
        "risk_sizing",
        "greeks",
        "metrics",
        "decision",
        "rejection_reasons",
        "data_freshness",
        "audit_meta",
        "execution_status",
    ]
    assert list(snap.keys()) == expected_keys


def test_strategy_snapshot_and_metrics_endpoints(tmp_path):
    app = _build_app(tmp_path)
    client = app.test_client()

    snap_res = client.get("/commodities/api/strategy-snapshot")
    assert snap_res.status_code == 200
    assert "strategy_snapshot" in snap_res.get_json()

    met_res = client.get("/commodities/api/metrics")
    assert met_res.status_code == 200
    met = met_res.get_json()
    assert "snapshot_id" in met
    assert "metrics" in met
    assert "data_freshness" in met
    assert "execution_status" in met

