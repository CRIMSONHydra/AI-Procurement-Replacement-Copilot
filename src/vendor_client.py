from __future__ import annotations

import os
import time
from urllib.parse import quote
import requests

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def get_vendor_risk(vendor_name: str, timeout_seconds: float = 3.0, retries: int = 1) -> dict:
    """Low-level API client. Retries transient failures only; 404 and other 4xx raise immediately."""
    base_url = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    url = f"{base_url}/vendor-risk/{quote(vendor_name, safe='')}"
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, timeout=timeout_seconds)
            if response.status_code not in TRANSIENT_STATUS or attempt == retries:
                response.raise_for_status()
                return response.json()
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("unreachable")
