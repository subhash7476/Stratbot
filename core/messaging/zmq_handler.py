import zmq
import json
import time
import logging
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)

class ZmqBase:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.context = zmq.Context.instance()
        self.socket = None
        self.address = f"tcp://{self.host}:{self.port}"

    def close(self):
        if self.socket:
            try:
                # Store ref and set to None first to avoid race conditions during shutdown
                sock = self.socket
                self.socket = None
                # Set linger to 0 to close immediately and release the port
                sock.setsockopt(zmq.LINGER, 0)
                sock.close()
            except:
                pass

class ZmqPublisher(ZmqBase):
    def __init__(self, host: str, port: int, bind: bool = True):
        super().__init__(host, port)
        self.socket = self.context.socket(zmq.PUB)
        self.socket.set(zmq.SNDHWM, 1000)
        if bind:
            self.socket.bind(self.address)
            logger.info(f"ZMQ Publisher bound to {self.address}")
        else:
            self.socket.connect(self.address)
            logger.info(f"ZMQ Publisher connected to {self.address}")

    def publish(self, topic: str, msg_type: str, data: Dict[str, Any], version: int = 1):
        """
        Publishes a message with the required envelope.
        """
        envelope = {
            "v": version,
            "type": msg_type,
            "topic": topic,
            "ts": time.time(),
            "data": data
        }
        try:
            if not self.socket:
                return
            # ZMQ PUB requires topic + space + message for string-based filtering
            message = f"{topic} {json.dumps(envelope)}"
            self.socket.send_string(message)
        except Exception as e:
            # Avoid logging if we're shutting down and the logger might be broken
            try:
                logger.error(f"Error publishing message to {topic}: {e}")
            except:
                pass

class ZmqSubscriber(ZmqBase):
    def __init__(self, host: str, port: int, topics: Optional[List[str]] = None, conflate: bool = False, hwm: int = 1000, bind: bool = False):
        super().__init__(host, port)
        self.socket = self.context.socket(zmq.SUB)
        
        if conflate:
            self.socket.set(zmq.CONFLATE, 1)
            # Conflate only keeps the latest, so HWM is naturally 1
            self.socket.set(zmq.RCVHWM, 1)
        else:
            self.socket.set(zmq.RCVHWM, hwm)
            
        if bind:
            self.socket.bind(self.address)
            logger.info(f"ZMQ Subscriber bound to {self.address}")
        else:
            self.socket.connect(self.address)
            logger.info(f"ZMQ Subscriber connected to {self.address}")
        
        if topics:
            for topic in topics:
                self.subscribe(topic)
        else:
            self.subscribe("") # Subscribe to all
            
        logger.info(f"ZMQ Subscriber connected to {self.address}")

    def subscribe(self, topic: str):
        self.socket.setsockopt_string(zmq.SUBSCRIBE, topic)
        logger.debug(f"Subscribed to topic: {topic}")

    def recv(self, timeout_ms: int = 0) -> Optional[Dict[str, Any]]:
        """
        Receives a message. If timeout_ms > 0, waits for the specified time.
        Returns the decoded JSON envelope if a message is received.
        """
        try:
            if timeout_ms > 0:
                if self.socket.poll(timeout_ms, zmq.POLLIN):
                    message = self.socket.recv_string(zmq.NOBLOCK)
                else:
                    return None
            else:
                message = self.socket.recv_string(zmq.NOBLOCK)
                
            # Split by the first space to separate topic and json payload
            _, payload = message.split(" ", 1)
            return json.loads(payload)
        except zmq.Again:
            return None
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            return None
