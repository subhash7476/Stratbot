"""
Alerter - Unified Alerting System
--------------------------------
Handles delivery of operational and trading alerts via multiple channels.
"""
import logging
from typing import Optional
from core.alerts.telegram_notifier import TelegramNotifier

class Alerter:
    """
    Central dispatcher for system alerts.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("Alerter")
        self.telegram = TelegramNotifier()

    def info(self, message: str):
        self.logger.info(message)
        self.telegram.send_message(f"ℹ️ *INFO*: {message}")

    def warning(self, message: str):
        self.logger.warning(message)
        self.telegram.send_message(f"⚠️ *WARNING*: {message}")

    def critical(self, message: str):
        self.logger.error(message)
        self.telegram.send_message(f"🔴 *CRITICAL*: {message}")

# Singleton instance
alerter = Alerter()
