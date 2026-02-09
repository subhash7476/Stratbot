# ZeroMQ Layer – Implementation Summary

## Overview

The ZeroMQ (ZMQ) layer provides the **real-time, low-latency event backbone** of the trading platform.  
It is designed to **accelerate live data flow** while preserving the platform’s core guarantees:

- Determinism
- Safety
- Process isolation
- Database-as-source-of-truth

ZMQ is used strictly for **ephemeral, best-effort events**.  
All persistence, replay, and authority remain with DuckDB and the execution layer.

---

## Design Principles

The ZMQ layer follows these non-negotiable principles:

1. **Observational, not authoritative**
   - No business state lives exclusively in ZMQ
   - Missed messages must be recoverable via DuckDB

2. **Best-effort and lossy**
   - No delivery guarantees
   - No retries
   - No acknowledgements

3. **Non-blocking**
   - ZMQ failures must never block or crash trading paths

4. **Process-decoupling**
   - All communication is inter-process
   - No shared memory between nodes

---

## ZMQ Patterns Used

### PUB / SUB (Primary Pattern)

Used for:
- Market data distribution
- Telemetry streaming

Characteristics:
- One-to-many fan-out
- Late subscribers allowed
- No delivery guarantees
- Extremely low latency

---

## Core Components

### 1. `ZmqPublisher`

**Purpose**
- Lightweight wrapper over ZMQ `PUB` sockets
- Used by all publishing nodes

**Key Properties**
- Fire-and-forget semantics
- No retries
- No blocking calls
- Errors are logged and swallowed

**Usage**
- Market data publishing
- Telemetry publishing

---

### 2. `ZmqSubscriber`

**Purpose**
- Wrapper over ZMQ `SUB` sockets
- Used by runners, scanners, and Flask bridge

**Key Features**
- Topic-based filtering
- Configurable:
  - `RCVHWM`
  - `CONFLATE` (latest-wins semantics)
- Graceful handling of disconnects
- Safe to start before publishers

---

## Market Data ZMQ Layer (Phase 1)

### Topics
market.candle.1m.{symbol}
### Flow
Market Data Node
→ ZMQ PUB
→ Strategy Runner(s)
→ Scanner Node(s)


### Safety Mechanism: Dual-Rail Model

- **Fast Path**: ZMQ events
- **Fallback Path**: DuckDB polling

Consumers:
- De-duplicate messages by timestamp
- Fall back to DuckDB if ZMQ stalls
- Never assume message completeness

---

## Process Decoupling (Phase 2)

### Node Responsibilities

#### Market Data Node
- Sole DuckDB writer
- Publishes market data via ZMQ
- Owns WebSocket ingestion and aggregation

#### Strategy Runner Node
- Read-only DuckDB access
- Consumes market data via ZMQ
- Owns execution authority

#### Scanner Node
- Read-only DuckDB access
- ZMQ consumer only
- Cannot execute trades

### Guarantees
- Independent restartability
- No shared memory
- No cross-process state mutation
- Hard enforcement of single DB writer

---

## Telemetry ZMQ Layer (Phase 3)

### Telemetry Topics
telemetry.metrics
telemetry.positions
telemetry.health.{node_name}
telemetry.logs.{node_name}


### TelemetryPublisher

**Purpose**
- Unified telemetry publishing interface for all nodes

**Properties**
- Stateless
- Exception-swallowing
- Non-blocking
- Safe to call from any process edge

**Publishing Model**
- Snapshot-based (not deltas)
- Timer-driven (not event-driven)
- Logs are lossy and bounded

---

## Backpressure & Flow Control

### Latest-Wins Strategy

For telemetry:
- `zmq.CONFLATE = True`
- `RCVHWM = 1`

Effect:
- Only the most recent telemetry snapshot is retained
- Older updates are dropped automatically
- Prevents memory growth and UI lag

Logs:
- Use rolling buffers instead of conflation

---

## Flask ZMQ → SSE Bridge

### Purpose
Expose telemetry to the browser without polling or coupling.

### Architecture
ZMQ SUB (background thread)
↓
In-memory latest-wins store
↓
SSE endpoint (/api/telemetry/stream)
↓
Browser EventSource

### Properties
- Read-only
- Best-effort
- Lossy
- Non-blocking
- Flask failure does not affect trading
- Trading failure does not affect Flask

---

## Failure Semantics

The ZMQ layer is explicitly designed to tolerate failure.

### Safe Failure Cases
- ZMQ publisher crashes
- ZMQ subscriber crashes
- Flask crashes
- Browser disconnects
- Message gaps
- Out-of-order delivery

### Guaranteed Outcomes
- Trading continues normally
- No duplicate executions
- No DB corruption
- Deterministic recovery from DuckDB state

---

## Explicit Non-Goals

ZMQ is **not** used for:
- Persistence
- Guaranteed delivery
- Ordering guarantees
- Execution authority
- UI control
- Inter-process commands (yet)

---

## Final Summary

The ZMQ layer provides a **high-performance, decoupled event fabric** that:

- Accelerates live data paths
- Preserves deterministic trading behavior
- Enables independent process scaling
- Adds real-time observability without risk

It is intentionally **minimal, lossy, and non-authoritative** by design.

> ZMQ acts as the nervous system — fast, transient, and replaceable —
> while DuckDB remains the memory and execution remains the brain.

---

## File Locations & Integration Points

### Core ZMQ Components
- `core/zmq/zmq_publisher.py`: Implements the `ZmqPublisher` class for publishing events via ZMQ PUB sockets
- `core/zmq/zmq_subscriber.py`: Implements the `ZmqSubscriber` class for consuming events via ZMQ SUB sockets
- `core/zmq/telemetry_publisher.py`: Provides the `TelemetryPublisher` class for unified system telemetry publishing

### Integration Points
- `flask_app/zmq_bridge.py`: Implements the Flask ZMQ→SSE bridge for real-time telemetry in the web UI
- `core/data/market_data_provider.py`: Integrates ZMQ for real-time market data distribution
- `core/execution/handler.py`: Uses ZMQ for real-time telemetry publishing
- `core/runner.py`: Subscribes to market data via ZMQ for real-time processing

---

**Status**: Implemented, verified, and frozen.
