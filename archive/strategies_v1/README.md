# Archive: Strategies V1 (Feb 2026)

Archived on 2026-02-28 as part of strategy cleanup.
All code remains in git history and here for reference.

## Why Archived

After 5 months of rigorous walk-forward testing, these strategies/filters
were proven dead or anti-productive:

- **Meta-model**: Anti-predictive on equities
- **Signal quality filters**: Kalman filter regime-dependent, catastrophic in hostile periods
- **HMM execution layer**: Not profitable on index (no volume data)
- **Demo strategies**: Never deployed (ehma_pivot, confluence_consumer, etc.)
- **Expansion v3**: Components built but never integrated

## What Was Kept (still in core/strategies/)

| File | Purpose |
|------|---------|
| base.py | Abstract base class |
| registry.py | Strategy factory (now only v9_pm_scalper) |
| v9_pm_scalper.py | Nifty PM scalper (registry version) |
| v9_pm_scalper_strategy.py | Nifty PM paper trading (standalone) |
| stock_daytype_paper.py | Stock DayTrade 10am strategy |
| precomputed_signals.py | Generic precomputed event feeder |
| pixityAI_batch_events.py | Vectorized event generation (equity backtest) |
| regime/observer.py | HMM regime observer (useful for future gating) |
| regime/classifier.py | HMM regime classifier (useful for future gating) |

## Archived Files

### strategies/
| File | What It Was | Why Archived |
|------|-------------|--------------|
| ehma_pivot.py | EHMA crossover demo | Never deployed |
| confluence_consumer.py | Multi-indicator aggregation | Never deployed |
| daily_regime_strategy_v2.py | Simple regime filter | Never deployed |
| regime_adaptive.py | MR/TF adaptive switching | Never deployed |
| premium_signal.py | Placeholder | Empty logic |
| premium_tp_sl.py | Premium signals w/ ADX+ATR | Research only |
| ultimate_trading_dashboard_strategy.py | Dashboard placeholder | Empty logic |
| pixityAI_event_generator.py | Single-event PixityAI | Superseded by batch version |
| pixityAIMetaStrategy.py | PixityAI + meta-model | Meta-model anti-predictive |

### strategies/expansion_v3/
| File | What It Was | Why Archived |
|------|-------------|--------------|
| daily_compression_scanner.py | EOD compression scanner | Never integrated |
| daily_breakout_trigger.py | EOD breakout confirmation | Never integrated |
| hourly_breakout_trigger.py | Intraday breakout execution | Never integrated |

### strategies/regime/
| File | What It Was | Why Archived |
|------|-------------|--------------|
| executor.py | HMM regime executor | Not profitable on index |
| sizing.py | Regime-based position sizing | Goes with executor |
| circuit_breaker.py | Weekly loss circuit breaker | Goes with executor |
| regime_config.json | HMM parameters | Goes with executor |

### filters/
| File | What It Was | Why Archived |
|------|-------------|--------------|
| base.py | Filter abstract base | Entire filter system archived |
| models.py | FilterResult, FilterContext | Entire filter system archived |
| registry.py | Filter plugin registry | Entire filter system archived |
| pipeline.py | Sequential/AND/OR/WEIGHTED pipeline | Entire filter system archived |
| kalman_filter.py | Kalman trend-alignment filter | Regime-dependent, catastrophic |
| volatility_filter.py | Volatility-based filter | Part of failed pipeline |
| ou_reversion_filter.py | Ornstein-Uhlenbeck filter | Part of failed pipeline |
| gmm_regime_filter.py | GMM regime filter | Part of failed pipeline |

### scripts/
| File | What It Was | Why Archived |
|------|-------------|--------------|
| pixityAI_trainer.py | Model training | Meta-model deprecated |
| pixityAI_backtest_runner.py | Batch backtest CLI | Superseded by runner.py |
| pixityAI_workflow.py | End-to-end workflow | Research complete |
| compare_filters_backtest.py | Filter A/B comparison | Research complete |
| compare_causal_fix.py | Causal swing A/B test | Research complete |
| run_regime_backtest.py | HMM backtest | Research complete |
| generate_regime_map.py | Regime visualization | Research complete |
| diagnostic_regime.py | Regime diagnostics | Research complete |
| validate_filters.py | Filter validation | Research complete |
| validate_premium_tp_sl_backtest.py | Premium strategy test | Never deployed |
| validate_pixityAI_live.py | Live validation | Research complete |
| validate_multi.py | Multi-symbol validation | Research complete |
| validate_single.py | Single-symbol validation | Research complete |

### models/
| File | What It Was | Why Archived |
|------|-------------|--------------|
| signal_quality_config.json | Kalman filter config | Filter system archived |
