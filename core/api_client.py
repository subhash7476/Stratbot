# core/api_client.py
"""
Streamlit API Client
====================

Helper module for Streamlit pages to interact with the FastAPI backend.
Provides simple functions that wrap API calls and handle errors gracefully.

Usage in Streamlit pages:
    from core.api_client import BackendClient

    client = BackendClient()

    # Start a scan (non-blocking!)
    scan_id = client.start_scan()

    # Poll for status
    status = client.get_scan_status(scan_id)

    # Get results when done
    if status["status"] == "completed":
        results = client.get_scan_results(scan_id)
"""

import requests
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import logging
import time

logger = logging.getLogger(__name__)


@dataclass
class APIConfig:
    """API configuration"""
    base_url: str = "http://127.0.0.1:8000"
    timeout: float = 30.0
    retry_count: int = 3
    retry_delay: float = 1.0


class BackendClient:
    """
    Client for interacting with the FastAPI backend.

    Handles connection errors gracefully - returns None or empty results
    instead of raising exceptions (better for Streamlit).
    """

    def __init__(self, config: Optional[APIConfig] = None):
        self.config = config or APIConfig()
        self._session = requests.Session()

    @property
    def base_url(self) -> str:
        return self.config.base_url

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None,
        timeout: Optional[float] = None
    ) -> Optional[Dict]:
        """
        Make HTTP request to backend.

        Returns None if request fails (doesn't raise exception).
        """
        url = f"{self.base_url}{endpoint}"
        timeout = timeout or self.config.timeout

        for attempt in range(self.config.retry_count):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    json=json,
                    params=params,
                    timeout=timeout
                )

                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    logger.warning(f"Not found: {endpoint}")
                    return None
                elif response.status_code == 429:
                    logger.warning("Rate limited, retrying...")
                    time.sleep(self.config.retry_delay * (attempt + 1))
                    continue
                else:
                    logger.error(f"API error {response.status_code}: {response.text}")
                    return None

            except requests.exceptions.ConnectionError:
                logger.warning(f"Backend not available (attempt {attempt + 1})")
                if attempt < self.config.retry_count - 1:
                    time.sleep(self.config.retry_delay)
                continue

            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout (attempt {attempt + 1})")
                if attempt < self.config.retry_count - 1:
                    time.sleep(self.config.retry_delay)
                continue

            except Exception as e:
                logger.error(f"Request error: {e}")
                return None

        return None

    def is_backend_available(self) -> bool:
        """Check if backend is running and healthy"""
        result = self._request("GET", "/health", timeout=5.0)
        return result is not None and result.get("status") in ("healthy", "degraded")

    # =========================================================================
    # MARKET DATA
    # =========================================================================

    def get_ltp(self, instrument_key: str) -> Optional[float]:
        """Get instant LTP for an instrument"""
        result = self._request("GET", f"/api/market/ltp/{instrument_key}")
        if result and result.get("success"):
            return result.get("ltp")
        return None

    def get_ltps(self, instrument_keys: List[str]) -> Dict[str, Optional[float]]:
        """Get LTPs for multiple instruments"""
        result = self._request(
            "POST",
            "/api/market/ltps",
            json={"instrument_keys": instrument_keys}
        )
        if result and result.get("success"):
            return result.get("data", {})
        return {}

    def get_all_ltps(self) -> Dict[str, float]:
        """Get all available LTPs"""
        result = self._request("GET", "/api/market/ltps/all")
        if result and result.get("success"):
            return result.get("data", {})
        return {}

    def get_quote(self, instrument_key: str) -> Optional[Dict]:
        """Get full quote for an instrument"""
        result = self._request("GET", f"/api/market/quote/{instrument_key}")
        if result and result.get("success"):
            return result.get("data")
        return None

    def get_market_status(self) -> Optional[Dict]:
        """Get market data system status"""
        return self._request("GET", "/api/market/status")

    # =========================================================================
    # SCANNER
    # =========================================================================

    def start_scan(self, config: Optional[Dict] = None, instruments: Optional[List[str]] = None) -> Optional[str]:
        """
        Start a new scan in the background.

        Returns scan_id for tracking, or None if failed.
        """
        payload = {}
        if config:
            payload["config"] = config
        if instruments:
            payload["instruments"] = instruments

        result = self._request("POST", "/api/scanner/start", json=payload if payload else None)

        if result and result.get("success"):
            return result.get("scan_id")
        return None

    def get_scan_status(self, scan_id: str) -> Optional[Dict]:
        """
        Get scan status and progress.

        Returns dict with: scan_id, status, progress, started_at, etc.
        """
        return self._request("GET", f"/api/scanner/status/{scan_id}")

    def get_scan_results(self, scan_id: str) -> Optional[Dict]:
        """
        Get scan results.

        Returns dict with: tradable_signals, ready_signals, etc.
        """
        return self._request("GET", f"/api/scanner/results/{scan_id}")

    def cancel_scan(self, scan_id: str) -> bool:
        """Cancel a running scan"""
        result = self._request("POST", f"/api/scanner/cancel/{scan_id}")
        return result is not None and result.get("success", False)

    def list_scans(self) -> List[Dict]:
        """List all scans"""
        result = self._request("GET", "/api/scanner/list")
        if result and result.get("success"):
            return result.get("scans", [])
        return []

    def get_active_scans(self) -> List[str]:
        """Get list of active scan IDs"""
        result = self._request("GET", "/api/scanner/active")
        if result and result.get("success"):
            return result.get("active_scans", [])
        return []

    def quick_scan(self) -> Optional[str]:
        """Start a quick scan with default settings"""
        result = self._request("POST", "/api/scanner/quick")
        if result and result.get("success"):
            return result.get("scan_id")
        return None

    # =========================================================================
    # SIGNALS
    # =========================================================================

    def list_signals(
        self,
        status: Optional[str] = None,
        strategy: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """List signals with optional filtering"""
        params = {"limit": limit}
        if status:
            params["status"] = status
        if strategy:
            params["strategy"] = strategy

        result = self._request("GET", "/api/signals", params=params)
        if result and result.get("success"):
            return result.get("signals", [])
        return []

    def get_signal(self, signal_id: str) -> Optional[Dict]:
        """Get a single signal by ID"""
        result = self._request("GET", f"/api/signals/{signal_id}")
        if result and result.get("success"):
            return result.get("signal")
        return None

    def create_signal(self, signal: Dict) -> Optional[str]:
        """Create a new signal"""
        result = self._request("POST", "/api/signals", json=signal)
        if result and result.get("success"):
            return result.get("signal", {}).get("signal_id")
        return None

    def update_signal(self, signal_id: str, updates: Dict) -> bool:
        """Update a signal"""
        result = self._request("PUT", f"/api/signals/{signal_id}", json=updates)
        return result is not None and result.get("success", False)

    def delete_signal(self, signal_id: str) -> bool:
        """Delete a signal"""
        result = self._request("DELETE", f"/api/signals/{signal_id}")
        return result is not None and result.get("success", False)

    def clear_signals(self, status: Optional[str] = None) -> bool:
        """Clear signals (optionally by status)"""
        params = {}
        if status:
            params["status"] = status

        result = self._request("POST", "/api/signals/clear", params=params)
        return result is not None and result.get("success", False)

    # =========================================================================
    # WEBSOCKET CONTROL
    # =========================================================================

    def start_websocket(self) -> bool:
        """Start the Upstox WebSocket connection"""
        result = self._request("POST", "/api/market/websocket/start")
        return result is not None and result.get("success", False)

    def stop_websocket(self) -> bool:
        """Stop the WebSocket connection"""
        result = self._request("POST", "/api/market/websocket/stop")
        return result is not None and result.get("success", False)


# ============================================================
# CONVENIENCE FUNCTIONS
# ============================================================

_client: Optional[BackendClient] = None


def get_client() -> BackendClient:
    """Get singleton client instance"""
    global _client
    if _client is None:
        _client = BackendClient()
    return _client


def is_backend_available() -> bool:
    """Check if backend is running"""
    return get_client().is_backend_available()


def start_scan_async(config: Optional[Dict] = None) -> Optional[str]:
    """
    Start a scan via backend (non-blocking).

    Use this instead of running scan directly in Streamlit.
    """
    return get_client().start_scan(config)


def get_scan_status(scan_id: str) -> Optional[Dict]:
    """Get scan status"""
    return get_client().get_scan_status(scan_id)


def get_scan_results(scan_id: str) -> Optional[Dict]:
    """Get scan results"""
    return get_client().get_scan_results(scan_id)


def poll_scan_until_complete(
    scan_id: str,
    poll_interval: float = 1.0,
    timeout: float = 300.0,
    progress_callback: Optional[callable] = None
) -> Optional[Dict]:
    """
    Poll scan status until complete.

    Args:
        scan_id: Scan ID to poll
        poll_interval: Seconds between polls
        timeout: Maximum wait time in seconds
        progress_callback: Optional callback(status_dict) for progress updates

    Returns:
        Final results dict or None if failed/timeout
    """
    client = get_client()
    start_time = time.time()

    while True:
        status = client.get_scan_status(scan_id)

        if status is None:
            logger.error(f"Failed to get status for scan {scan_id}")
            return None

        if progress_callback:
            progress_callback(status)

        scan_status = status.get("status")

        if scan_status == "completed":
            return client.get_scan_results(scan_id)

        if scan_status in ("failed", "cancelled"):
            return status

        # Check timeout
        if time.time() - start_time > timeout:
            logger.warning(f"Scan {scan_id} timed out after {timeout}s")
            client.cancel_scan(scan_id)
            return None

        time.sleep(poll_interval)
