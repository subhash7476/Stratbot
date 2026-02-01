# Operational Change Proposal Template

## 📅 Date: 2026-02-01
## 👤 Proposer: opencode

## 🎯 Summary of Change
Systematic decoupling of market ingestion from the UI and Execution layers.

## 🛠️ Implementation Plan
1. Promote WebSocketIngestor to a standalone daemon.
2. Implement DB-to-DB aggregation.
3. Update Runner to consume from DuckDB only.

## ✅ Verification Steps
- Smoke test verification.
- Idempotency check on signal generation.
- Latency audit on bar completion.
