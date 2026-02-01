"""
Telegram Notifier
-----------------
Lightweight, fire-and-forget delivery of alerts to Telegram.
"""
import requests
import os
import logging
from threading import Thread

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Asynchronous Telegram notification handler.
    """
    
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}/sendMessage" if self.token else None

    def send_message(self, text: str):
        """Dispatches a message in a background thread."""
        if not self.base_url or not self.chat_id:
            logger.debug("Telegram token or chat ID not set. Alert suppressed.")
            return

        Thread(target=self._dispatch, args=(text,), daemon=True).start()

    def _dispatch(self, text: str):
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            requests.post(self.base_url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
