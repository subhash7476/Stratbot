import sys
import os
import time
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.messaging.zmq_handler import ZmqPublisher
from core.database.manager import DatabaseManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MockPublisher")

def run_mock_publisher(symbol: str = "NSE_EQ|INE002A01018"):
    zmq_config_path = ROOT / "config" / "zmq.json"
    with open(zmq_config_path, "r") as f:
        zmq_config = json.load(f)
        
    db_manager = DatabaseManager(ROOT / "data")
    publisher = ZmqPublisher(
        host=zmq_config["host"],
        port=zmq_config["ports"]["market_data_pub"]
    )
    
    logger.info(f"Mock Publisher started for {symbol}. Broadcasting and writing to DuckDB every 5s.")
    
    current_ts = datetime.now().replace(second=0, microsecond=0)
    
    try:
        while True:
            # Broadcast CURRENT minute every 1.5s (similar to real ingestor)
            current_ts = datetime.now().replace(second=0, microsecond=0)
            topic = f"market.candle.1m.{symbol}"
            data = {
                "symbol": symbol,
                "timeframe": "1m",
                "timestamp": current_ts.isoformat(),
                "open": 2500.0,
                "high": 2510.0,
                "low": 2490.0,
                "close": 2505.0,
                "volume": 1000
            }
            
            # 1. Publish to ZMQ (Fast-path)
            publisher.publish(topic, "market_candle", data)
            logger.info(f"Broadcasted candle for {symbol} at {current_ts}")

            # 2. Write to DuckDB (Simulate ingestor persistence)
            with db_manager.live_buffer_writer() as conns:
                conns['candles'].execute(
                    """
                    INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume, is_synthetic)
                    VALUES (?, '1m', ?, ?, ?, ?, ?, ?, FALSE)
                    ON CONFLICT (symbol, timeframe, timestamp) DO UPDATE SET
                        open = EXCLUDED.open,
                        high = EXCLUDED.high,
                        low = EXCLUDED.low,
                        close = EXCLUDED.close,
                        volume = EXCLUDED.volume
                    """,
                    [symbol, current_ts, data["open"], data["high"], data["low"], data["close"], data["volume"]]
                )
            logger.info(f"Saved candle to DuckDB for {symbol}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Stopping mock publisher...")
    finally:
        publisher.close()

if __name__ == "__main__":
    run_mock_publisher()
