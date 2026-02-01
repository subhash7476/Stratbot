"""
Session Logger
--------------
Markdown-based event logging for operational audit trails.
"""
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

class SessionLogger:
    """
    Produces human-readable session logs.
    """
    
    def __init__(self, logs_dir: str = "logs/sessions"):
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._current_file = None

    def start_session(self, metadata: Dict[str, Any]):
        date_str = datetime.now().strftime("%Y%m%d")
        self._current_file = self.logs_dir / f"session_{date_str}.md"
        
        with open(self._current_file, "a") as f:
            f.write(f"\n# 🚀 Trading Session: {datetime.now().isoformat()}\n")
            f.write(f"**Mode**: {metadata.get('mode')}\n")
            f.write(f"**Symbols**: {metadata.get('symbols')}\n")
            f.write(f"**Strategies**: {metadata.get('strategies')}\n\n")

    def log_event(self, section: str, message: str):
        if not self._current_file: return
        with open(self._current_file, "a") as f:
            f.write(f"### {section}\n")
            f.write(f"- [{datetime.now().time()}] {message}\n\n")

    def close_session(self):
        self._current_file = None
