"""
Signal Quality Pipeline - Orchestrates multiple filters.
"""

from typing import List, Dict, Any, Optional
from enum import Enum
import logging
import json
import pandas as pd

from core.filters.base import BaseSignalFilter
from core.filters.models import FilterResult, FilterContext
from core.filters.registry import FilterRegistry


logger = logging.getLogger(__name__)


class PipelineMode(Enum):
    """Modes for combining multiple filter results."""

    SEQUENTIAL = "SEQUENTIAL"
    """Stop at first rejection (short-circuit, order matters)"""

    AND = "AND"
    """All filters must pass"""

    OR = "OR"
    """At least one filter must pass"""

    WEIGHTED = "WEIGHTED"
    """Aggregate confidence scores, require minimum threshold"""


class SignalQualityPipeline:
    """
    Orchestrates multiple signal quality filters.

    Filters can be combined in different modes (AND/OR/WEIGHTED/SEQUENTIAL)
    to create flexible quality control pipelines.
    """

    def __init__(
        self,
        filters: List[BaseSignalFilter],
        mode: PipelineMode = PipelineMode.SEQUENTIAL,
        min_confidence_threshold: float = 0.6,
        enabled: bool = True
    ):
        """
        Initialize the pipeline.

        Args:
            filters: List of filter instances (in order for SEQUENTIAL mode)
            mode: How to combine filter results
            min_confidence_threshold: Minimum confidence for WEIGHTED mode
            enabled: Global enable/disable for entire pipeline
        """
        self.filters = filters
        self.mode = mode
        self.min_confidence_threshold = min_confidence_threshold
        self.enabled = enabled

        # Stats tracking
        self.total_evaluated = 0
        self.total_accepted = 0
        self.total_rejected = 0

        logger.info(
            f"SignalQualityPipeline initialized: "
            f"mode={mode.value}, filters={len(filters)}, enabled={enabled}"
        )

    @classmethod
    def from_config(cls, config_path: str) -> 'SignalQualityPipeline':
        """
        Create pipeline from JSON config file.

        Args:
            config_path: Path to signal_quality_config.json

        Returns:
            Configured pipeline instance
        """
        with open(config_path, 'r') as f:
            config = json.load(f)

        pipeline_config = config.get('signal_quality_pipeline', {})

        if not pipeline_config.get('enabled', True):
            logger.info("Pipeline disabled in config, creating pass-through pipeline")
            return cls(filters=[], enabled=False)

        # Parse mode
        mode_str = pipeline_config.get('mode', 'SEQUENTIAL')
        mode = PipelineMode[mode_str]

        # Create filter instances
        filters = []
        for filter_cfg in pipeline_config.get('filters', []):
            if not filter_cfg.get('enabled', True):
                continue

            name = filter_cfg['name']
            params = filter_cfg.get('params', {})
            weight = filter_cfg.get('weight', 1.0)

            # Merge weight into params
            params['enabled'] = True
            params['weight'] = weight

            filter_instance = FilterRegistry.create(name, params)
            filters.append(filter_instance)

        min_threshold = pipeline_config.get('min_confidence_threshold', 0.6)

        logger.info(f"Loaded {len(filters)} filters from config: {[f.filter_name for f in filters]}")

        return cls(
            filters=filters,
            mode=mode,
            min_confidence_threshold=min_threshold,
            enabled=True
        )

    def initialize(self, market_data: pd.DataFrame) -> None:
        """
        Initialize all filters with historical data.

        Args:
            market_data: Historical OHLCV bars for filter initialization
        """
        if not self.enabled:
            logger.info("Pipeline disabled, skipping initialization")
            return

        logger.info(f"Initializing {len(self.filters)} filters...")
        for filter_instance in self.filters:
            logger.info(f"  Initializing {filter_instance.filter_name}...")
            filter_instance.initialize(market_data)
            filter_instance.is_initialized = True

        logger.info("All filters initialized successfully")

    def evaluate(self, context: FilterContext) -> FilterResult:
        """
        Evaluate a signal through the filter pipeline.

        Args:
            context: Signal and market context for evaluation

        Returns:
            Aggregated FilterResult (pass/fail, confidence, reason)
        """
        self.total_evaluated += 1

        # If pipeline disabled, always pass
        if not self.enabled or len(self.filters) == 0:
            return FilterResult(
                passed=True,
                confidence=1.0,
                reason="Pipeline disabled or no filters configured",
                filter_name="pipeline",
                metadata={"mode": "passthrough"}
            )

        # Collect results from all filters
        filter_results = []

        if self.mode == PipelineMode.SEQUENTIAL:
            # Stop at first rejection
            for filter_instance in self.filters:
                result = filter_instance.evaluate(context)
                filter_results.append(result)

                if not result.passed:
                    # Short-circuit on rejection
                    self.total_rejected += 1
                    return self._aggregate_results(filter_results, mode=self.mode)

            # All passed
            self.total_accepted += 1
            return self._aggregate_results(filter_results, mode=self.mode)

        else:
            # Evaluate all filters (AND/OR/WEIGHTED modes)
            for filter_instance in self.filters:
                result = filter_instance.evaluate(context)
                filter_results.append(result)

            aggregated = self._aggregate_results(filter_results, mode=self.mode)

            if aggregated.passed:
                self.total_accepted += 1
            else:
                self.total_rejected += 1

            return aggregated

    def _aggregate_results(
        self,
        results: List[FilterResult],
        mode: PipelineMode
    ) -> FilterResult:
        """
        Aggregate multiple filter results into a single decision.

        Args:
            results: List of FilterResults from individual filters
            mode: How to combine results

        Returns:
            Aggregated FilterResult
        """
        if not results:
            return FilterResult(
                passed=True,
                confidence=1.0,
                reason="No filters evaluated",
                filter_name="pipeline"
            )

        if mode == PipelineMode.SEQUENTIAL:
            # Return last result (or first rejection if any)
            final_result = results[-1]
            return FilterResult(
                passed=final_result.passed,
                confidence=final_result.confidence,
                reason=final_result.reason,
                filter_name="pipeline",
                metadata={
                    "mode": "SEQUENTIAL",
                    "filter_chain": [r.filter_name for r in results],
                    "individual_results": [
                        {"filter": r.filter_name, "passed": r.passed, "reason": r.reason}
                        for r in results
                    ]
                }
            )

        elif mode == PipelineMode.AND:
            # All must pass
            all_passed = all(r.passed for r in results)
            avg_confidence = sum(r.confidence for r in results) / len(results)

            if all_passed:
                reason = f"All {len(results)} filters passed"
            else:
                failed = [r.filter_name for r in results if not r.passed]
                reason = f"Rejected by: {', '.join(failed)}"

            return FilterResult(
                passed=all_passed,
                confidence=avg_confidence,
                reason=reason,
                filter_name="pipeline",
                metadata={
                    "mode": "AND",
                    "individual_results": [
                        {"filter": r.filter_name, "passed": r.passed, "confidence": r.confidence}
                        for r in results
                    ]
                }
            )

        elif mode == PipelineMode.OR:
            # At least one must pass
            any_passed = any(r.passed for r in results)
            max_confidence = max(r.confidence for r in results)

            if any_passed:
                passed_by = [r.filter_name for r in results if r.passed]
                reason = f"Accepted by: {', '.join(passed_by)}"
            else:
                reason = f"All {len(results)} filters rejected"

            return FilterResult(
                passed=any_passed,
                confidence=max_confidence,
                reason=reason,
                filter_name="pipeline",
                metadata={
                    "mode": "OR",
                    "individual_results": [
                        {"filter": r.filter_name, "passed": r.passed, "confidence": r.confidence}
                        for r in results
                    ]
                }
            )

        elif mode == PipelineMode.WEIGHTED:
            # Weighted confidence score
            total_weight = sum(f.weight for f in self.filters if f.enabled)
            weighted_confidence = sum(
                r.confidence * f.weight for r, f in zip(results, self.filters)
            ) / total_weight

            passed = weighted_confidence >= self.min_confidence_threshold

            reason = (
                f"Weighted confidence: {weighted_confidence:.3f} "
                f"{'≥' if passed else '<'} {self.min_confidence_threshold}"
            )

            return FilterResult(
                passed=passed,
                confidence=weighted_confidence,
                reason=reason,
                filter_name="pipeline",
                metadata={
                    "mode": "WEIGHTED",
                    "threshold": self.min_confidence_threshold,
                    "individual_results": [
                        {
                            "filter": r.filter_name,
                            "confidence": r.confidence,
                            "weight": f.weight,
                            "weighted_score": r.confidence * f.weight
                        }
                        for r, f in zip(results, self.filters)
                    ]
                }
            )

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics."""
        total = self.total_evaluated
        acceptance_rate = (self.total_accepted / total * 100) if total > 0 else 0

        return {
            "total_evaluated": total,
            "total_accepted": self.total_accepted,
            "total_rejected": self.total_rejected,
            "acceptance_rate_pct": acceptance_rate,
            "mode": self.mode.value,
            "enabled": self.enabled,
            "num_filters": len(self.filters),
            "filter_names": [f.filter_name for f in self.filters]
        }

    def __repr__(self) -> str:
        return (
            f"SignalQualityPipeline(mode={self.mode.value}, "
            f"filters={len(self.filters)}, enabled={self.enabled})"
        )
