import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from core.messaging.telemetry import TelemetryPublisher


# Global set to track configured loggers and prevent duplicate handlers
_configured_loggers = set()


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: Optional[int] = None,
    telemetry_publisher: Optional[TelemetryPublisher] = None,
    console: bool = True
):
    """
    Setup a logger with rotating file handler, console handler, and optional telemetry handler.
    
    Args:
        name: Logger name (will write to logs/<name>.log by default)
        log_file: Optional custom log file path
        level: Logging level (defaults to config.settings.LOG_LEVEL)
        telemetry_publisher: Optional TelemetryPublisher for sending logs via ZMQ
        console: Whether to add console handler
    
    Returns:
        Configured logger instance
    """
    # Import here to avoid circular imports
    try:
        from config.settings import LOG_LEVEL
    except ImportError:
        LOG_LEVEL = "INFO"
    
    # Convert string level to int if needed
    if level is None:
        level_str = LOG_LEVEL
        level = getattr(logging, level_str.upper(), logging.INFO)
    elif isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Prevent adding duplicate handlers if logger was already configured
    if name in _configured_loggers:
        return logger
    
    # Set propagate to False to prevent root logger duplication
    logger.propagate = False
    
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Set up file handler with rotation (10MB, 5 backups)
    if log_file is None:
        log_file = logs_dir / f"{name}.log"
    else:
        log_file = Path(log_file)
        # Ensure parent directory exists
        log_file.parent.mkdir(parents=True, exist_ok=True)
    
    file_handler = RotatingFileHandler(
        str(log_file),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)
    
    # Add console handler if requested
    if console:
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)
    
    # Add telemetry handler if publisher provided
    if telemetry_publisher is not None:
        telemetry_handler = TelemetryHandler(telemetry_publisher)
        telemetry_handler.setLevel(level)
        logger.addHandler(telemetry_handler)
    
    # Mark logger as configured to prevent duplicate handlers
    _configured_loggers.add(name)
    
    return logger


class TelemetryHandler(logging.Handler):
    """
    Custom logging handler that forwards log records to TelemetryPublisher.
    Exception-safe and non-blocking to prevent logging from interfering with main operations.
    """
    
    def __init__(self, telemetry_publisher: TelemetryPublisher):
        super().__init__()
        self.telemetry_publisher = telemetry_publisher
    
    def emit(self, record):
        """
        Emit a log record by forwarding it to the TelemetryPublisher.
        This method is exception-safe and non-blocking.
        """
        try:
            # Format the log record
            message = self.format(record)
            
            # Map logging levels to appropriate telemetry log methods
            level = record.levelname.lower()
            
            # Publish the log via the telemetry publisher
            # Using a non-blocking approach to prevent logging from affecting main operations
            self.telemetry_publisher.publish_log(level, message)
        except Exception:
            # If there's an issue with telemetry publishing, silently ignore
            # to prevent logging from breaking the main application
            pass