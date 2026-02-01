# 🚀 Trading Platform

A production-grade, deterministic algorithmic trading platform built with Python, DuckDB, and Upstox V2.

## 🌟 Key Features
- **Deterministic Loop**: Single-threaded runner ensuring backtest/live parity.
- **Database-First**: DuckDB as the single source of truth for all state.
- **High Fidelity**: Sub-minute tick journaling and automated recovery.
- **Production Safety**: Multi-level kill switches and automated alerting.

## 🛠️ Tech Stack
- **Engine**: Python 3.10+
- **Database**: DuckDB
- **Persistence**: JSON/CSV Artifacts
- **Broker**: Upstox V2 (REST + WebSocket)
- **UI**: Flask + Tailwind CSS

## 🚀 Quick Start
1.  **Install Dependencies**: `pip install -r requirements.txt`
2.  **Initialize DB**: `python scripts/init_db.py`
3.  **Run Dashboard**: `python scripts/run_flask.py`

For detailed architecture, see `PROJECT_MASTER.md`.
