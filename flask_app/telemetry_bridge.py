import threading
import json
import logging
import time
from queue import Queue, Empty
from typing import Dict, Any, List
from core.messaging.zmq_handler import ZmqSubscriber

logger = logging.getLogger(__name__)

class TelemetryBridge:
    """
    ZMQ-to-SSE Bridge.
    Runs in a background thread and forwards ZMQ telemetry to internal subscribers.
    """
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.subscribers: List[Queue] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="TelemetryBridgeThread", daemon=True)
        self._thread.start()
        logger.info(f"Telemetry bridge started on {self.host}:{self.port}")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def subscribe(self) -> Queue:
        """Returns a new queue for a client to listen to."""
        q = Queue(maxsize=10) # Small HWM for SSE clients
        with self._lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def _run(self):
        # telemetry topics: telemetry.metrics.*, telemetry.positions.*, telemetry.health.*, telemetry.logs.*
        subscriber = ZmqSubscriber(
            host=self.host, 
            port=self.port, 
            topics=["telemetry"],
            conflate=False # We want to handle different types, and conflate might lose logs
        )
        
        while not self._stop_event.is_set():
            try:
                # Use a small timeout to allow checking stop_event
                envelope = subscriber.recv(timeout_ms=500)
                if not envelope:
                    continue
                
                # Push to all active SSE queues
                with self._lock:
                    for q in self.subscribers:
                        if q.full():
                            try:
                                q.get_nowait() # Drop oldest if full (Backpressure)
                            except:
                                pass
                        q.put_nowait(envelope)
                        
            except Exception as e:
                logger.error(f"Error in telemetry bridge loop: {e}")
                time.sleep(1) # Backoff
        
        subscriber.close()
        logger.info("Telemetry bridge thread exiting.")

# Singleton instance
bridge: Optional[TelemetryBridge] = None

def get_telemetry_bridge(host: str = "127.0.0.1", port: int = 5560) -> TelemetryBridge:
    global bridge
    if bridge is None:
        bridge = TelemetryBridge(host, port)
        bridge.start()
    return bridge
