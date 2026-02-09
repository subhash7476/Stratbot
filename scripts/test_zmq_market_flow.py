import sys
import os
import time
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.messaging.zmq_handler import ZmqSubscriber
from core.database.manager import DatabaseManager
from core.database.providers.live_market import LiveDuckDBMarketDataProvider
from core.database.providers.zmq_market import ZmqMarketDataProvider

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ZmqVerification")

def run_raw_subscriber(symbol: str, zmq_config: dict, timeout: int = 70):
    logger.info(f"--- 1. Testing Raw ZMQ Subscriber for {symbol} ---")
    subscriber = ZmqSubscriber(
        host=zmq_config["host"],
        port=zmq_config["ports"]["market_data_pub"],
        topics=[f"market.candle.1m.{symbol}"]
    )
    
    logger.info("Waiting for first ZMQ message... (Ingestor must be running)")
    start_time = time.time()
    received = False
    
    while time.time() - start_time < timeout:
        envelope = subscriber.recv(timeout_ms=1000)
        if envelope:
            recv_time = time.time()
            data = envelope["data"]
            pub_time = envelope["ts"]
            latency = (recv_time - pub_time) * 1000
            logger.info(f"SUCCESS: ZMQ Received | TS: {data['timestamp']} | Latency: {latency:.2f}ms")
            received = True
            break
            
    subscriber.close()
    if not received:
        logger.warning("FAILED: No ZMQ message received within timeout.")
    return received

def run_failure_test(symbol: str, zmq_config: dict, db_manager: DatabaseManager):
    logger.info(f"--- 2. Testing Failure & Fallback for {symbol} ---")
    
    provider = ZmqMarketDataProvider(
        symbols=[symbol],
        zmq_host=zmq_config["host"],
        zmq_port=zmq_config["ports"]["market_data_pub"],
        db_manager=db_manager
    )
    
    logger.info("Step A: Provider initialized. Requesting bar (should fallback to DB if ZMQ empty)...")
    bar = provider.get_next_bar(symbol)
    if bar:
        logger.info(f"Bar retrieved (source likely DuckDB): {bar.timestamp}")
    else:
        logger.info("No bar available yet.")

    logger.info("Step B: Please KILL the Ingestor / ZMQ Publisher now. Waiting 10s...")
    time.sleep(10)
    
    logger.info("Requesting bar again (should still work via DuckDB fallback)...")
    bar = provider.get_next_bar(symbol)
    if bar:
        logger.info(f"Bar retrieved after publisher kill: {bar.timestamp}")
    else:
        logger.info("No new bar in DuckDB.")

    logger.info("Step C: Please RESTART the Ingestor / ZMQ Publisher now. Waiting 10s...")
    time.sleep(10)

    logger.info("Requesting bar (should resume ZMQ reception)...")
    bar = provider.get_next_bar(symbol)
    if bar:
        logger.info(f"Bar retrieved after publisher restart: {bar.timestamp}")

    provider.stop()

def run_latency_comparison(symbol: str, zmq_config: dict, db_manager: DatabaseManager):
    logger.info(f"--- 3. Latency Comparison for {symbol} ---")
    
    # 1. ZMQ Latency
    subscriber = ZmqSubscriber(
        host=zmq_config["host"],
        port=zmq_config["ports"]["market_data_pub"],
        topics=[f"market.candle.1m.{symbol}"]
    )
    
    # 2. DuckDB Poller
    duckdb_provider = LiveDuckDBMarketDataProvider(symbols=[symbol], db_manager=db_manager, poll_interval=0.1)
    
    # HACK: Rewind DuckDB poller's last timestamp to ensure it sees the next mock candle as 'new'
    if symbol in duckdb_provider._last_timestamps:
        from datetime import timedelta
        duckdb_provider._last_timestamps[symbol] -= timedelta(minutes=2)
    
    logger.info("Waiting for NEXT ZMQ message first...")
    
    seen_ts = None
    start_time = time.time()
    
    while time.time() - start_time < 70:
        envelope = subscriber.recv(timeout_ms=1)
        if envelope:
            zmq_arrival = time.time()
            seen_ts = envelope["data"]["timestamp"]
            logger.info(f"ZMQ Arrived: {datetime.now()} for {seen_ts}")
            
            # Now immediately look for this TS in DuckDB
            db_poll_start = time.time()
            while time.time() - db_poll_start < 10:
                bar = duckdb_provider.get_next_bar(symbol)
                if bar:
                    ts_str = bar.timestamp.isoformat()
                    if ts_str == seen_ts:
                        db_arrival = time.time()
                        logger.info(f"DuckDB Poll Arrived: {datetime.now()} for {seen_ts}")
                        diff = (db_arrival - zmq_arrival) * 1000
                        logger.info(f"RESULT: ZMQ was {diff:.2f}ms faster than DuckDB polling")
                        return True
                time.sleep(0.01)
            logger.warning("ZMQ received but DuckDB never showed the bar.")
            break
        time.sleep(0.001)
    return False

    subscriber.close()
    duckdb_provider.stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NSE_EQ|INE002A01018", help="Instrument key (e.g. RELIANCE)")
    parser.add_argument("--mode", choices=["flow", "failure", "latency", "all"], default="all")
    args = parser.parse_args()

    zmq_config_path = ROOT / "config" / "zmq.json"
    with open(zmq_config_path, "r") as f:
        zmq_config = json.load(f)
        
    db_manager = DatabaseManager(ROOT / "data")

    if args.mode in ["flow", "all"]:
        run_raw_subscriber(args.symbol, zmq_config)
    
    if args.mode in ["latency", "all"]:
        run_latency_comparison(args.symbol, zmq_config, db_manager)

    if args.mode in ["failure", "all"]:
        run_failure_test(args.symbol, zmq_config, db_manager)

if __name__ == "__main__":
    main()
