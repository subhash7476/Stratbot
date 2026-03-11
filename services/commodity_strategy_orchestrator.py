"""Commodity strategy orchestrator for MCX Gold options snapshots and auto-trigger execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from math import floor
from typing import Any, Dict, List, Optional, Sequence, Tuple
import json

from analytics.options_greeks import compute_greeks_with_iv
from analytics.structured_metrics import build_strategy_metrics_snapshot
from core.backtest.usdinr_attribution import build_usdinr_attribution_report
from core.database.manager import DatabaseManager
from core.database.queries import MarketDataQuery
from core.events import SignalEvent, SignalType
from risk.premium_risk_model import map_underlying_stop_to_premium_risk
from strategy.filters.liquidity_filter import LiquidityCheckInput, evaluate_liquidity
from strategy.options.strike_selector import OptionCandidate, select_best_strike
from strategy.regime.volatility_regime import classify_volatility_regime


class PositionState(str, Enum):
    FLAT = "FLAT"
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    PENDING_EXIT = "PENDING_EXIT"


class ExecutionStatus(str, Enum):
    NONE = "NONE"
    EXECUTED = "EXECUTED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    SKIPPED_COOLDOWN = "SKIPPED_COOLDOWN"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class CommodityStrategySnapshot:
    """Deterministic commodity strategy snapshot contract."""

    snapshot_id: str
    regime: Dict[str, Any]
    strike_selection: Dict[str, Any]
    liquidity_check: Dict[str, Any]
    risk_sizing: Dict[str, Any]
    greeks: Dict[str, Any]
    metrics: Dict[str, Any]
    decision: str
    rejection_reasons: List[str]
    data_freshness: Dict[str, Any]
    audit_meta: Dict[str, Any]
    execution_status: str = ExecutionStatus.NONE.value

    def to_dict(self) -> Dict[str, Any]:
        """Return fixed-order dictionary for deterministic API/UI output."""
        return {
            "snapshot_id": self.snapshot_id,
            "regime": self.regime,
            "strike_selection": self.strike_selection,
            "liquidity_check": self.liquidity_check,
            "risk_sizing": self.risk_sizing,
            "greeks": self.greeks,
            "metrics": self.metrics,
            "decision": self.decision,
            "rejection_reasons": self.rejection_reasons,
            "data_freshness": self.data_freshness,
            "audit_meta": self.audit_meta,
            "execution_status": self.execution_status,
        }


class CommodityStrategyOrchestrator:
    """Builds snapshots and optionally triggers option executions."""

    snapshot_interval_seconds = 5

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        execution_handler: Optional[Any] = None,
        *,
        gold_underlying_key: str = "MCX_FO|GOLD",
        usdinr_key: str = "MCX_FO|USDINR",
        risk_pct: float = 0.005,
        atr_multiplier: float = 1.0,
        option_chain_max_age_seconds: int = 10,
        usdinr_max_age_seconds: int = 30,
        cooldown_seconds: int = 30,
    ):
        self.db = db_manager or DatabaseManager("data")
        self.market_query = MarketDataQuery(self.db)
        self.execution_handler = execution_handler
        self.gold_underlying_key = gold_underlying_key
        self.usdinr_key = usdinr_key
        self.risk_pct = risk_pct
        self.atr_multiplier = atr_multiplier
        self.option_chain_max_age_seconds = option_chain_max_age_seconds
        self.usdinr_max_age_seconds = usdinr_max_age_seconds
        self.cooldown_seconds = cooldown_seconds

        self._last_snapshot: Optional[CommodityStrategySnapshot] = None
        self._last_snapshot_timestamp: Optional[datetime] = None
        self._executed_snapshot_ids: set[str] = set()
        self._last_execution_by_contract: Dict[str, datetime] = {}
        self._state_by_contract: Dict[str, PositionState] = {}
        self._locked_contract: Optional[str] = None

        self._ensure_snapshot_table()

    def _ensure_snapshot_table(self) -> None:
        with self.db.trading_writer() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS commodity_strategy_snapshots (
                    timestamp TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    regime TEXT,
                    selected_strike REAL,
                    liquidity_pass INTEGER,
                    risk_size INTEGER,
                    decision TEXT,
                    rejection_reason TEXT,
                    metrics_json TEXT,
                    snapshot_json TEXT
                )
                """
            )

    def get_latest_snapshot(self, now: datetime) -> CommodityStrategySnapshot:
        """Compute (or return guarded) snapshot and process auto-trigger execution."""
        if self._last_snapshot_timestamp and (now - self._last_snapshot_timestamp).total_seconds() < self.snapshot_interval_seconds:
            if self._last_snapshot is not None:
                return self._last_snapshot

        market_inputs = self._load_market_inputs(now)
        snapshot = self._build_snapshot(now, market_inputs)
        snapshot = self._maybe_execute(snapshot, now)
        self._persist_snapshot(snapshot, now)

        self._last_snapshot = snapshot
        self._last_snapshot_timestamp = now
        return snapshot

    def build_usdinr_attribution(self, run_id_without: str, run_id_with: str) -> Dict[str, Any]:
        """Build attribution report for two completed runs."""
        def _load_net_pnls(run_id: str) -> List[float]:
            with self.db.backtest_reader(run_id) as conn:
                rows = conn.execute("SELECT pnl, fees FROM trades").fetchall()
            return [float((r[0] or 0.0) - (r[1] or 0.0)) for r in rows]

        report = build_usdinr_attribution_report(
            pnls_without_usdinr_filter=_load_net_pnls(run_id_without),
            pnls_with_usdinr_filter=_load_net_pnls(run_id_with),
        )
        return report.to_dict()

    def _load_market_inputs(self, now: datetime) -> Dict[str, Any]:
        underlying_price, underlying_ts = self._latest_underlying_price(now)
        usd_feat = self._usdinr_features(now)

        expiries = self._available_expiries()
        selected_expiry = self._select_expiry(now, expiries)

        chain_rows, chain_ts = self._option_chain_rows(selected_expiry)
        equity = self._account_equity()

        return {
            "underlying_price": underlying_price,
            "underlying_timestamp": underlying_ts,
            "usdinr": usd_feat,
            "selected_expiry": selected_expiry,
            "option_chain_rows": chain_rows,
            "option_chain_timestamp": chain_ts,
            "account_equity": equity,
            "iv_history": self._iv_history(),
            "slippage_samples": self._slippage_samples(),
        }

    def _build_snapshot(self, now: datetime, inputs: Dict[str, Any]) -> CommodityStrategySnapshot:
        rejection_reasons: List[str] = []

        option_age = self._age_seconds(now, inputs.get("option_chain_timestamp"))
        usdinr_age = self._age_seconds(now, inputs.get("usdinr", {}).get("timestamp"))
        data_fresh = option_age <= self.option_chain_max_age_seconds and usdinr_age <= self.usdinr_max_age_seconds
        if option_age > self.option_chain_max_age_seconds:
            rejection_reasons.append("stale_option_chain")
        if usdinr_age > self.usdinr_max_age_seconds:
            rejection_reasons.append("stale_usdinr")

        rv20 = float(inputs.get("usdinr", {}).get("realized_volatility_20d", 0.0))
        rv5 = float(inputs.get("usdinr", {}).get("realized_volatility_5d", 0.0))

        option_rows = inputs.get("option_chain_rows", [])
        iv_values = [float(r.get("iv", 0.0) or 0.0) for r in option_rows if r.get("iv") is not None]
        option_iv = float(sum(iv_values) / len(iv_values)) if iv_values else 0.0
        atr = float(inputs.get("usdinr", {}).get("atr", 0.0))

        regime = classify_volatility_regime(
            realized_volatility_20d=rv20 if rv20 > 0 else 0.2,
            realized_volatility_5d=rv5,
            option_implied_volatility=option_iv,
            atr=atr,
        )

        # Contract lock: while pending/open, keep selected contract unchanged and skip reselection.
        locked = self._locked_contract if self._locked_contract and self._state_by_contract.get(self._locked_contract) in {PositionState.PENDING_ENTRY, PositionState.OPEN} else None
        strike_result, selected_contract = self._select_strike(inputs, option_rows, locked)
        if strike_result.get("selected") is None:
            rejection_reasons.append("no_strike_selected")

        liquidity = {
            "trade_allowed": False,
            "spread_pct": 1.0,
            "reasons": ["no_candidate"],
        }
        if selected_contract is not None:
            liq_res = evaluate_liquidity(
                LiquidityCheckInput(
                    bid=float(selected_contract.bid),
                    ask=float(selected_contract.ask),
                    open_interest=int(selected_contract.open_interest),
                    last_trade_time=inputs.get("option_chain_timestamp") or now,
                    now=now,
                    premium=max((float(selected_contract.bid) + float(selected_contract.ask)) / 2.0, 0.0),
                )
            )
            liquidity = {
                "trade_allowed": bool(liq_res.trade_allowed),
                "spread_pct": float(liq_res.spread_pct),
                "reasons": list(liq_res.reasons),
            }
            if not liq_res.trade_allowed:
                rejection_reasons.append("liquidity_filter_failed")

        risk_sizing = {
            "expected_underlying_move": 0.0,
            "estimated_premium_move": 0.0,
            "risk_per_contract": 0.0,
            "risk_budget": 0.0,
            "max_contracts": 0,
        }
        if selected_contract is not None:
            pr = map_underlying_stop_to_premium_risk(
                account_equity=float(inputs.get("account_equity", 0.0)),
                risk_pct=self.risk_pct,
                underlying_atr=max(atr, 1e-6),
                atr_multiplier=self.atr_multiplier,
                option_delta=float(selected_contract.delta),
                lot_size=int(selected_contract.open_interest > 0 and 1 or 1),
            )
            risk_sizing = {
                "expected_underlying_move": float(pr.expected_underlying_move),
                "estimated_premium_move": float(pr.estimated_premium_move),
                "risk_per_contract": float(pr.risk_per_contract),
                "risk_budget": float(pr.risk_budget),
                "max_contracts": int(pr.max_contracts),
            }
            if pr.max_contracts <= 0:
                rejection_reasons.append("risk_size_zero")

        greeks = {
            "delta": 0.0,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "implied_volatility": 0.0,
        }
        if selected_contract is not None:
            premium = max((float(selected_contract.bid) + float(selected_contract.ask)) / 2.0, 0.01)
            tte = self._time_to_expiry_years(now, inputs.get("selected_expiry"))
            g = compute_greeks_with_iv(
                underlying_futures_price=float(inputs.get("underlying_price", 0.0)),
                strike=float(selected_contract.strike),
                time_to_expiry=max(tte, 1e-6),
                risk_free_rate=0.06,
                option_price=premium,
                option_type=selected_contract.option_type,
            )
            greeks = {
                "delta": float(g.delta[0]),
                "gamma": float(g.gamma[0]),
                "theta": float(g.theta[0]),
                "vega": float(g.vega[0]),
                "implied_volatility": float(g.implied_volatility[0]),
            }

        metrics_obj = build_strategy_metrics_snapshot(
            current_iv=option_iv,
            iv_history=inputs.get("iv_history", []),
            realized_volatility=max(rv20, 0.0),
            spread_pcts=[float(((r.get("ask") or 0.0) - (r.get("bid") or 0.0)) / max(((r.get("ask") or 0.0) + (r.get("bid") or 0.0)) / 2.0, 1e-8)) for r in option_rows if r.get("ask") is not None and r.get("bid") is not None],
            liquidity_rejections=1 if not liquidity.get("trade_allowed", False) else 0,
            liquidity_checks=1,
            slippage_bps_samples=inputs.get("slippage_samples", []),
        )
        metrics = metrics_obj.to_dict()

        if not data_fresh:
            decision = "REJECT"
        elif rejection_reasons:
            decision = "REJECT"
        else:
            decision = "ACCEPT"

        timestamp_bucket = floor(now.timestamp() / self.snapshot_interval_seconds)
        risk_size = int(risk_sizing.get("max_contracts", 0))
        selected_payload = strike_result.get("selected") or {}
        strike = selected_payload.get("strike")
        snapshot_id = self._snapshot_hash(
            regime=regime.regime.value,
            expiry=str(inputs.get("selected_expiry") or ""),
            strike=str(strike or ""),
            liquidity_pass=bool(liquidity.get("trade_allowed", False)),
            risk_size=risk_size,
            timestamp_bucket=timestamp_bucket,
        )

        audit_meta = {
            "timestamp": now.isoformat(),
            "timestamp_bucket": timestamp_bucket,
            "snapshot_interval_seconds": self.snapshot_interval_seconds,
            "expiry": inputs.get("selected_expiry"),
            "underlying_symbol": self.gold_underlying_key,
            "selected_contract": strike_result.get("selected"),
        }

        return CommodityStrategySnapshot(
            snapshot_id=snapshot_id,
            regime={
                "regime": regime.regime.value,
                "iv_hv_ratio": float(regime.iv_hv_ratio),
                "short_long_rv_ratio": float(regime.short_long_rv_ratio),
                "atr_norm": float(regime.atr_norm),
                "confidence": float(regime.confidence),
            },
            strike_selection=strike_result,
            liquidity_check=liquidity,
            risk_sizing=risk_sizing,
            greeks=greeks,
            metrics=metrics,
            decision=decision,
            rejection_reasons=rejection_reasons,
            data_freshness={
                "data_fresh": data_fresh,
                "option_chain_age_seconds": option_age,
                "usdinr_data_age_seconds": usdinr_age,
            },
            audit_meta=audit_meta,
            execution_status=ExecutionStatus.NONE.value,
        )

    def _maybe_execute(self, snapshot: CommodityStrategySnapshot, now: datetime) -> CommodityStrategySnapshot:
        status = ExecutionStatus.NONE
        reasons = list(snapshot.rejection_reasons)

        # Required guard ordering:
        # 1) data freshness 2) liquidity 3) dedupe 4) cooldown 5) state machine 6) execution
        if not snapshot.data_freshness.get("data_fresh", False):
            status = ExecutionStatus.REJECTED
            return self._replace_status(snapshot, status)

        if not snapshot.liquidity_check.get("trade_allowed", False):
            status = ExecutionStatus.REJECTED
            return self._replace_status(snapshot, status)

        if snapshot.snapshot_id in self._executed_snapshot_ids:
            status = ExecutionStatus.SKIPPED_DUPLICATE
            return self._replace_status(snapshot, status)

        contract_key = str(snapshot.audit_meta.get("selected_contract", {}).get("instrument_key") or "")
        if contract_key:
            last_exec_ts = self._last_execution_by_contract.get(contract_key)
            if last_exec_ts and (now - last_exec_ts).total_seconds() < self.cooldown_seconds:
                status = ExecutionStatus.SKIPPED_COOLDOWN
                return self._replace_status(snapshot, status)

        current_state = self._state_by_contract.get(contract_key, PositionState.FLAT)
        if current_state in {PositionState.PENDING_ENTRY, PositionState.OPEN}:
            status = ExecutionStatus.REJECTED
            return self._replace_status(snapshot, status)

        if snapshot.decision != "ACCEPT":
            status = ExecutionStatus.REJECTED
            return self._replace_status(snapshot, status)

        if self.execution_handler is None:
            status = ExecutionStatus.REJECTED
            if "execution_handler_unavailable" not in reasons:
                reasons.append("execution_handler_unavailable")
            return CommodityStrategySnapshot(
                snapshot_id=snapshot.snapshot_id,
                regime=snapshot.regime,
                strike_selection=snapshot.strike_selection,
                liquidity_check=snapshot.liquidity_check,
                risk_sizing=snapshot.risk_sizing,
                greeks=snapshot.greeks,
                metrics=snapshot.metrics,
                decision="REJECT",
                rejection_reasons=reasons,
                data_freshness=snapshot.data_freshness,
                audit_meta=snapshot.audit_meta,
                execution_status=status.value,
            )

        contract = snapshot.audit_meta.get("selected_contract", {})
        signal = SignalEvent(
            strategy_id="mcx_gold_options_orchestrator",
            symbol=self.gold_underlying_key,
            timestamp=now,
            signal_type=SignalType.BUY,
            confidence=float(snapshot.regime.get("confidence", 0.5)),
            metadata={
                "signal_id": snapshot.snapshot_id,
                "execution_mode": "option",
                "selected_contract": contract,
                "quantity": int(snapshot.risk_sizing.get("max_contracts", 0)),
                "liquidity_pass_gate": True,
                "sl_distance": max(float(snapshot.risk_sizing.get("expected_underlying_move", 0.0)), 0.01),
                "risk_r": 1.0,
            },
        )

        order = self.execution_handler.process_signal(signal, float(snapshot.audit_meta.get("selected_contract", {}).get("underlying_ltp", 0.0) or 0.0) or 1.0)
        if order is not None:
            self._executed_snapshot_ids.add(snapshot.snapshot_id)
            if contract_key:
                self._last_execution_by_contract[contract_key] = now
                self._state_by_contract[contract_key] = PositionState.PENDING_ENTRY
                self._locked_contract = contract_key
            status = ExecutionStatus.EXECUTED
        else:
            status = ExecutionStatus.REJECTED

        return self._replace_status(snapshot, status)

    def _replace_status(self, snapshot: CommodityStrategySnapshot, status: ExecutionStatus) -> CommodityStrategySnapshot:
        return CommodityStrategySnapshot(
            snapshot_id=snapshot.snapshot_id,
            regime=snapshot.regime,
            strike_selection=snapshot.strike_selection,
            liquidity_check=snapshot.liquidity_check,
            risk_sizing=snapshot.risk_sizing,
            greeks=snapshot.greeks,
            metrics=snapshot.metrics,
            decision=snapshot.decision,
            rejection_reasons=snapshot.rejection_reasons,
            data_freshness=snapshot.data_freshness,
            audit_meta=snapshot.audit_meta,
            execution_status=status.value,
        )

    def _persist_snapshot(self, snapshot: CommodityStrategySnapshot, now: datetime) -> None:
        selected = snapshot.strike_selection.get("selected") or {}
        selected_strike = selected.get("strike")
        rejection_reason = ",".join(snapshot.rejection_reasons) if snapshot.rejection_reasons else ""

        with self.db.trading_writer() as conn:
            conn.execute(
                """
                INSERT INTO commodity_strategy_snapshots
                (timestamp, snapshot_id, regime, selected_strike, liquidity_pass, risk_size, decision, rejection_reason, metrics_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    now.isoformat(),
                    snapshot.snapshot_id,
                    snapshot.regime.get("regime"),
                    float(selected_strike) if selected_strike is not None else None,
                    1 if snapshot.liquidity_check.get("trade_allowed", False) else 0,
                    int(snapshot.risk_sizing.get("max_contracts", 0)),
                    snapshot.decision,
                    rejection_reason,
                    json.dumps(snapshot.metrics),
                    json.dumps(snapshot.to_dict()),
                ],
            )

    def _latest_underlying_price(self, now: datetime) -> Tuple[float, datetime]:
        try:
            bar = self.market_query.get_latest_bar(self.gold_underlying_key, exchange="mcx", timeframe="1m")
            if bar:
                ts = bar.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                return float(bar.get("close", 0.0) or 0.0), ts or now
        except Exception:
            pass
        return 0.0, now - timedelta(hours=1)

    def _usdinr_features(self, now: datetime) -> Dict[str, Any]:
        try:
            df = self.market_query.get_ohlcv(self.usdinr_key, timeframe="1m", limit=500)
            if df is None or df.empty:
                return {
                    "timestamp": now - timedelta(hours=1),
                    "realized_volatility_20d": 0.0,
                    "realized_volatility_5d": 0.0,
                    "atr": 0.0,
                }
            closes = df["close"].astype(float)
            rets = closes.pct_change().dropna()
            rv20 = float(rets.tail(20).std() * (252 ** 0.5)) if len(rets) >= 2 else 0.0
            rv5 = float(rets.tail(5).std() * (252 ** 0.5)) if len(rets) >= 2 else 0.0
            atr = float((df["high"].astype(float) - df["low"].astype(float)).tail(14).mean()) if len(df) > 0 else 0.0
            ts = df["timestamp"].iloc[-1]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            return {
                "timestamp": ts,
                "realized_volatility_20d": rv20,
                "realized_volatility_5d": rv5,
                "atr": atr,
            }
        except Exception:
            return {
                "timestamp": now - timedelta(hours=1),
                "realized_volatility_20d": 0.0,
                "realized_volatility_5d": 0.0,
                "atr": 0.0,
            }

    def _available_expiries(self) -> List[str]:
        expiries: List[str] = []
        try:
            with self.db.config_reader() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT expiry_date
                    FROM option_chain_snapshot
                    WHERE UPPER(underlying_symbol) LIKE '%GOLD%'
                    ORDER BY expiry_date ASC
                    """
                ).fetchall()
            expiries = [str(r[0]) for r in rows if r and r[0]]
        except Exception:
            expiries = []
        if not expiries:
            today = datetime.now().date()
            expiries = [(today + timedelta(days=7)).isoformat(), (today + timedelta(days=14)).isoformat()]
        return expiries

    def _select_expiry(self, now: datetime, expiries: Sequence[str]) -> Optional[str]:
        if not expiries:
            return None
        first = expiries[0]
        try:
            exp = datetime.fromisoformat(first).date()
            remaining = self._trading_days_remaining(now.date(), exp)
            if remaining < 3 and len(expiries) > 1:
                return expiries[1]
        except Exception:
            pass
        return first

    def _option_chain_rows(self, expiry: Optional[str]) -> Tuple[List[Dict[str, Any]], Optional[datetime]]:
        rows: List[Dict[str, Any]] = []
        latest_ts: Optional[datetime] = None
        if not expiry:
            return rows, latest_ts
        try:
            with self.db.config_reader() as conn:
                raw = conn.execute(
                    """
                    SELECT snapshot_timestamp, strike_price, option_type, instrument_key, tradingsymbol,
                           COALESCE(delta, 0.0) as delta, COALESCE(oi, 0) as oi,
                           COALESCE(iv, 0.0) as iv,
                           COALESCE(ltp, 0.0) as ltp
                    FROM option_chain_snapshot
                    WHERE UPPER(underlying_symbol) LIKE '%GOLD%'
                      AND expiry_date = ?
                    ORDER BY snapshot_timestamp DESC
                    LIMIT 200
                    """,
                    [expiry],
                ).fetchall()
            for r in raw:
                ts = r[0]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                latest_ts = max(latest_ts, ts) if latest_ts else ts
                ltp = float(r[8] or 0.0)
                bid = max(0.01, ltp * 0.995)
                ask = max(bid + 0.01, ltp * 1.005)
                rows.append(
                    {
                        "snapshot_timestamp": ts,
                        "strike": float(r[1]),
                        "option_type": str(r[2]),
                        "instrument_key": str(r[3] or ""),
                        "trading_symbol": str(r[4] or ""),
                        "delta": float(r[5] or 0.0),
                        "open_interest": int(r[6] or 0),
                        "iv": float(r[7] or 0.0),
                        "bid": bid,
                        "ask": ask,
                        "underlying_ltp": 0.0,
                    }
                )
        except Exception:
            rows = []

        if latest_ts is None:
            latest_ts = datetime.now() - timedelta(hours=1)
        return rows, latest_ts

    def _select_strike(
        self,
        inputs: Dict[str, Any],
        option_rows: List[Dict[str, Any]],
        locked_contract_key: Optional[str],
    ) -> Tuple[Dict[str, Any], Optional[OptionCandidate]]:
        if locked_contract_key:
            for r in option_rows:
                if str(r.get("instrument_key")) == locked_contract_key:
                    c = OptionCandidate(
                        strike=float(r.get("strike", 0.0)),
                        option_type=str(r.get("option_type", "CE")),
                        delta=float(r.get("delta", 0.0)),
                        bid=float(r.get("bid", 0.0)),
                        ask=float(r.get("ask", 0.0)),
                        open_interest=int(r.get("open_interest", 0)),
                        instrument_key=str(r.get("instrument_key", "")),
                        trading_symbol=str(r.get("trading_symbol", "")),
                    )
                    result = {
                        "selected": {
                            "strike": c.strike,
                            "option_type": c.option_type,
                            "instrument_key": c.instrument_key,
                            "trading_symbol": c.trading_symbol,
                            "delta": c.delta,
                            "bid": c.bid,
                            "ask": c.ask,
                            "open_interest": c.open_interest,
                            "locked": True,
                        },
                        "reason": "Contract lock active",
                        "score": 1.0,
                        "evaluated": 1,
                    }
                    return result, c

        candidates = [
            OptionCandidate(
                strike=float(r.get("strike", 0.0)),
                option_type=str(r.get("option_type", "CE")),
                delta=float(r.get("delta", 0.0)),
                bid=float(r.get("bid", 0.0)),
                ask=float(r.get("ask", 0.0)),
                open_interest=int(r.get("open_interest", 0)),
                instrument_key=str(r.get("instrument_key", "")),
                trading_symbol=str(r.get("trading_symbol", "")),
            )
            for r in option_rows
        ]

        direction = "buy"
        mode = "breakout"
        res = select_best_strike(
            underlying_price=float(inputs.get("underlying_price", 0.0)),
            option_chain_snapshot=candidates,
            direction=direction,
            signal_mode=mode,
        )
        selected = res.selected
        selected_payload = None
        if selected:
            selected_payload = {
                "strike": selected.strike,
                "option_type": selected.option_type,
                "instrument_key": selected.instrument_key,
                "trading_symbol": selected.trading_symbol,
                "delta": selected.delta,
                "bid": selected.bid,
                "ask": selected.ask,
                "open_interest": selected.open_interest,
                "locked": False,
            }

        return (
            {
                "selected": selected_payload,
                "reason": res.reason,
                "score": float(res.score),
                "evaluated": int(res.evaluated),
            },
            selected,
        )

    def _account_equity(self) -> float:
        if self.execution_handler is not None:
            metrics = getattr(self.execution_handler, "metrics", None)
            if metrics is not None and hasattr(metrics, "cash_balance"):
                return float(getattr(metrics, "cash_balance", 0.0))
        return 100000.0

    def _iv_history(self) -> List[float]:
        vals: List[float] = []
        try:
            with self.db.config_reader() as conn:
                rows = conn.execute(
                    """
                    SELECT iv FROM option_chain_snapshot
                    WHERE UPPER(underlying_symbol) LIKE '%GOLD%'
                      AND iv IS NOT NULL
                    ORDER BY snapshot_timestamp DESC
                    LIMIT 252
                    """
                ).fetchall()
            vals = [float(r[0]) for r in rows if r and r[0] is not None]
        except Exception:
            vals = []
        return vals

    def _slippage_samples(self) -> List[float]:
        return [0.0]

    @staticmethod
    def _time_to_expiry_years(now: datetime, expiry: Optional[str]) -> float:
        if not expiry:
            return 1 / 365
        try:
            exp_dt = datetime.fromisoformat(expiry)
            secs = max((exp_dt - now).total_seconds(), 1.0)
            return secs / (365.0 * 24.0 * 3600.0)
        except Exception:
            return 1 / 365

    @staticmethod
    def _trading_days_remaining(start: datetime.date, end: datetime.date) -> int:
        if end <= start:
            return 0
        d = start
        count = 0
        while d < end:
            d = d + timedelta(days=1)
            if d.weekday() < 5:
                count += 1
        return count

    @staticmethod
    def _age_seconds(now: datetime, ts: Optional[datetime]) -> float:
        if ts is None:
            return 1e9
        return max((now - ts).total_seconds(), 0.0)

    @staticmethod
    def _snapshot_hash(
        *,
        regime: str,
        expiry: str,
        strike: str,
        liquidity_pass: bool,
        risk_size: int,
        timestamp_bucket: int,
    ) -> str:
        payload = f"{regime}|{expiry}|{strike}|{int(liquidity_pass)}|{risk_size}|{timestamp_bucket}"
        return sha256(payload.encode("utf-8")).hexdigest()




