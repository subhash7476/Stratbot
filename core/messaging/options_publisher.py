"""
Options Structural Data Publisher
---------------------------------
Publishes options structural metrics via ZMQ for real-time distribution.

Architecture:
- Single backend fetcher (5s cycle) → ZMQ PUB → Multiple subscribers
- Subscribers: Flask SSE bridge, analytics engines, recording

Usage:
    publisher = OptionsPublisher("127.0.0.1", 5555)
    publisher.publish_structural_data(structural_data)
"""

import time
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from core.logging import setup_logger
from core.messaging.zmq_handler import ZmqPublisher

logger = setup_logger("options_publisher")


class OptionsPublisher:
    """
    Publishes options structural data via ZMQ.
    
    Topics:
    - options.structural: Complete structural snapshot (PCR, GEX, OI)
    - options.chain: Raw option chain data
    - options.summary: Key metrics summary
    
    Message Format:
    {
        "v": 1,
        "type": "options.structural",
        "topic": "options.structural",
        "ts": <timestamp>,
        "data": { ... }
    }
    """
    
    # Topic names
    TOPIC_STRUCTURAL = "options.structural"
    TOPIC_CHAIN = "options.chain"
    TOPIC_SUMMARY = "options.summary"
    
    def __init__(self, host: str = "127.0.0.1", port: int = 5557, bind: bool = True):
        """
        Initialize Options Publisher.
        
        Args:
            host: ZMQ host
            port: ZMQ port (default: 5557 for options)
            bind: If True, bind to port; if False, connect
        """
        self._publisher = ZmqPublisher(host, port, bind=bind)
        self._last_publish_time: Dict[str, datetime] = {}
        self._publish_count = 0
    
    def publish_structural_data(
        self,
        index: str,
        structural_data: Dict[str, Any],
        force: bool = False
    ):
        """
        Publish structural snapshot.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            structural_data: Complete structural data dict
            force: Force publish even if < 5s since last
        """
        # Rate limit to 5 seconds
        topic = f"{self.TOPIC_STRUCTURAL}.{index}"
        if not force and topic in self._last_publish_time:
            age = (datetime.now() - self._last_publish_time[topic]).total_seconds()
            if age < 5:
                return
        
        envelope = {
            "index": index,
            "underlying": structural_data.get("underlying", ""),
            "underlying_ltp": structural_data.get("underlying_ltp", 0),
            "expiry": structural_data.get("expiry", ""),
            "timestamp": structural_data.get("timestamp", ""),
            "atm_strike": structural_data.get("atm_strike"),
            "pcr": structural_data.get("pcr", {}),
            "gex": structural_data.get("gex", {}),
            "oi_analysis": structural_data.get("oi_analysis", {}),
            "max_pain": structural_data.get("max_pain"),
            "total_strikes": structural_data.get("total_strikes", 0),
            "last_update": structural_data.get("last_update", "")
        }
        
        self._publisher.publish(
            topic=topic,
            msg_type="options.structural",
            data=envelope
        )
        
        self._last_publish_time[topic] = datetime.now()
        self._publish_count += 1
        
        logger.debug(f"[OptionsPublisher] Published structural data for {index}")
    
    def publish_chain_data(
        self,
        index: str,
        chain_data: list,
        force: bool = False
    ):
        """
        Publish raw option chain data.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            chain_data: List of option contract dicts
            force: Force publish even if < 5s since last
        """
        topic = f"{self.TOPIC_CHAIN}.{index}"
        if not force and topic in self._last_publish_time:
            age = (datetime.now() - self._last_publish_time[topic]).total_seconds()
            if age < 5:
                return
        
        envelope = {
            "index": index,
            "chain": chain_data,
            "timestamp": datetime.now().isoformat()
        }
        
        self._publisher.publish(
            topic=topic,
            msg_type="options.chain",
            data=envelope
        )
        
        self._last_publish_time[topic] = datetime.now()
    
    def publish_summary(
        self,
        index: str,
        summary: Dict[str, Any],
        force: bool = False
    ):
        """
        Publish key metrics summary.
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            summary: Summary metrics dict
            force: Force publish even if < 5s since last
        """
        topic = f"{self.TOPIC_SUMMARY}.{index}"
        if not force and topic in self._last_publish_time:
            age = (datetime.now() - self._last_publish_time[topic]).total_seconds()
            if age < 5:
                return
        
        envelope = {
            "index": index,
            "underlying_ltp": summary.get("underlying_ltp", 0),
            "pcr": summary.get("pcr", 0),
            "pcr_sentiment": summary.get("pcr_sentiment", "Neutral"),
            "net_gamma": summary.get("net_gamma", 0),
            "gex_regime": summary.get("gex_regime", "Neutral"),
            "zero_gamma_level": summary.get("zero_gamma_level"),
            "resistance_strike": summary.get("resistance_strike"),
            "support_strike": summary.get("support_strike"),
            "timestamp": datetime.now().isoformat()
        }
        
        self._publisher.publish(
            topic=topic,
            msg_type="options.summary",
            data=envelope
        )
        
        self._last_publish_time[topic] = datetime.now()
    
    def publish_all(
        self,
        index: str,
        structural_data: Dict[str, Any],
        chain_data: Optional[list] = None,
        force: bool = False
    ):
        """
        Publish all options data (structural + chain + summary).
        
        Args:
            index: "NIFTY" or "BANKNIFTY"
            structural_data: Complete structural data
            chain_data: Optional raw chain data
            force: Force publish
        """
        self.publish_structural_data(index, structural_data, force)
        self.publish_summary(index, structural_data, force)
        
        if chain_data:
            self.publish_chain_data(index, chain_data, force)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get publisher statistics."""
        return {
            "publish_count": self._publish_count,
            "last_publish_time": {
                k: v.isoformat() for k, v in self._last_publish_time.items()
            }
        }
    
    def close(self):
        """Close ZMQ connection."""
        self._publisher.close()
        logger.info(f"[OptionsPublisher] Closed. Total publishes: {self._publish_count}")


class OptionsPoller:
    """
    Background poller that fetches and publishes options data.
    
    Runs in a separate thread, fetching data every 5 seconds
    and publishing via ZMQ.
    
    Usage:
        poller = OptionsPoller()
        poller.start()
        # ... runs in background ...
        poller.stop()
    """
    
    def __init__(
        self,
        zmq_host: str = "127.0.0.1",
        zmq_port: int = 5557,
        indices: Optional[list] = None
    ):
        """
        Initialize Options Poller.
        
        Args:
            zmq_host: ZMQ host
            zmq_port: ZMQ port
            indices: List of indices to track (default: ["NIFTY", "BANKNIFTY"])
        """
        from app_facade.options_facade import OptionsFacade
        
        self._facade = OptionsFacade()
        self._publisher = OptionsPublisher(zmq_host, zmq_port, bind=True)
        self._indices = indices or ["NIFTY", "BANKNIFTY"]
        self._running = False
        self._thread = None
    
    def start(self):
        """Start background polling thread."""
        import threading
        
        if self._thread:
            return
        
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="OptionsPoller",
            daemon=True
        )
        self._thread.start()
        
        logger.info(f"[OptionsPoller] Started for indices: {self._indices}")
    
    def stop(self):
        """Stop background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        
        self._publisher.close()
        logger.info("[OptionsPoller] Stopped")
    
    def _run(self):
        """Main polling loop."""
        import time

        while self._running:
            try:
                for index in self._indices:
                    if not self._running:
                        break

                    try:
                        structural = self._facade.get_structural_data(index)
                        chain = self._facade.get_option_chain(index)

                        structural_dict = self._facade.to_dict(structural)

                        self._publisher.publish_all(
                            index=index,
                            structural_data=structural_dict,
                            chain_data=chain,
                            force=True
                        )

                        logger.debug(f"[OptionsPoller] Published data for {index}")

                    except Exception as e:
                        logger.error(f"[OptionsPoller] Error fetching {index}: {e}")

                # Wait 5 seconds
                for _ in range(50):  # 50 x 0.1s = 5s
                    if not self._running:
                        break
                    time.sleep(0.1)

            except Exception as e:
                logger.error(f"[OptionsPoller] Loop error: {e}")
                time.sleep(1)  # Backoff on error
        
        logger.info("[OptionsPoller] Loop exiting")
