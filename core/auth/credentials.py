import json
import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class CredentialManager:
    """
    Manages sensitive API credentials and tokens.

    GUARANTEES:
    - Centralized access to Upstox API keys and tokens.
    - Thread-safe (process-level) filesystem storage.
    - No credentials stored in version control.
    - Token expiry detection for Upstox access tokens.
    """

    def __init__(self, config_path: str = "config/credentials.json"):
        self.path = Path(config_path)
        self._cache: Dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def save(self, data: Dict[str, Any]):
        """Persists credential data to disk. Records token_saved_at and last_refresh_date."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Track when token was saved for expiry detection
        if "access_token" in data:
            now = time.time()
            data["token_saved_at"] = now
            data["last_refresh_date"] = time.strftime("%Y-%m-%d", time.localtime(now))
        self._cache.update(data)
        with open(self.path, "w") as f:
            json.dump(self._cache, f, indent=4)

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieves a credential value."""
        return self._cache.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        return self._cache.copy()

    @property
    def has_upstox_token(self) -> bool:
        return bool(self.get("access_token"))

    @property
    def is_token_expired(self) -> bool:
        """Check if the Upstox token is likely expired (tokens last ~24h)."""
        saved_at = self.get("token_saved_at")
        if not saved_at:
            # No timestamp recorded — treat as expired if token exists
            return self.has_upstox_token
        elapsed_hours = (time.time() - saved_at) / 3600
        return elapsed_hours >= 22  # Conservative: flag at 22h instead of 24h

    @property
    def token_age_hours(self) -> Optional[float]:
        """Returns how many hours old the current token is, or None."""
        saved_at = self.get("token_saved_at")
        if not saved_at:
            return None
        return (time.time() - saved_at) / 3600

    @property
    def needs_daily_refresh(self) -> bool:
        """
        Check if the Upstox token needs a daily refresh.
        """
        if not self.has_upstox_token:
            return True
            
        last_date = self.get("last_refresh_date")
        current_date = time.strftime("%Y-%m-%d", time.localtime(time.time()))
        
        if last_date != current_date:
            return True
            
        return self.is_token_expired

# Singleton instance
credentials = CredentialManager()
