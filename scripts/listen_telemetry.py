import sys
import json
from pathlib import Path

# Add root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.messaging.zmq_handler import ZmqSubscriber

import argparse

def listen_telemetry(port_override: int = None):
    zmq_config_path = ROOT / "config" / "zmq.json"
    with open(zmq_config_path, "r") as f:
        zmq_config = json.load(f)
        
    port = port_override or zmq_config["ports"]["telemetry_pub"]
    print(f"Listening for telemetry on {zmq_config['host']}:{port}...")
    
    subscriber = ZmqSubscriber(
        host=zmq_config["host"],
        port=port,
        topics=["telemetry"]
    )
    
    try:
        while True:
            envelope = subscriber.recv(timeout_ms=1000)
            if envelope:
                print(f"[{envelope['topic']}] {json.dumps(envelope['data'], indent=2)}")
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        subscriber.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, help="Override telemetry port")
    args = parser.parse_args()
    listen_telemetry(port_override=args.port)
