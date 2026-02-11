"""
Execution Engine Invariants
---------------------------
Constitutional rules that must be satisfied before any execution logic proceeds.
These are hard gates; violations are considered non-recoverable logic errors.
"""

from typing import Set, Any

class ExecutionRuleError(Exception):
    """Raised when an execution invariant is violated."""
    pass

def enforce_signal_idempotency(signal_id: str, seen_signals: Set[str]):
    """Ensures a signal is never processed more than once in the current session."""
    if signal_id in seen_signals:
        raise ExecutionRuleError(f"Idempotency violation: Signal {signal_id} has already been processed.")

def enforce_risk_clearance(risk_approved: bool, reason: str = "Unknown"):
    """Ensures execution never proceeds if risk management has rejected the intent."""
    if not risk_approved:
        raise ExecutionRuleError(f"Risk clearance violation: {reason}")

def enforce_execution_authority(authorized: bool):
    """Ensures that execution flows only originate from the authorized ExecutionHandler intake."""
    if not authorized:
        raise ExecutionRuleError("Authority violation: Execution logic triggered from unauthorized path.")
