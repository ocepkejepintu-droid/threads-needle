"""Image hosting proxy for external uploads (0x0.st — no API key required)."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

ZEROXOST_UPLOAD_URL = "https://0x0.st"

# Shared HTTP client with connection pooling
_httpx_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.Client(timeout=60.0)
    return _httpx_client


def upload_image_file(
    data: bytes, filename: str | None = None, content_type: str | None = None
) -> str:
    """Upload image bytes to 0x0.st and return the public direct URL.

    Raises:
        RuntimeError: If upload fails.
    """
    client = _get_client()
    last_exc: Exception | None = None

    # Retry with simple backoff
    for attempt in range(1, 4):
        try:
            resp = client.post(
                ZEROXOST_UPLOAD_URL,
                files={"file": (filename or "image.jpg", data, content_type or "image/jpeg")},
            )
            resp.raise_for_status()
            link = resp.text.strip()
            if not link or not link.startswith("http"):
                raise RuntimeError(f"Unexpected response from 0x0.st: {link[:200]}")

            log.info("Uploaded image to 0x0.st: %s", link)
            return link

        except httpx.HTTPStatusError as exc:
            # Don't retry 4xx client errors
            if exc.response.status_code < 500:
                raise RuntimeError(f"Failed to upload to 0x0.st: {exc.response.status_code}") from exc
            last_exc = exc
            log.warning("0x0.st upload attempt %d failed: %s", attempt, exc)
            import time
            time.sleep(attempt * 1.5)
        except httpx.HTTPError as exc:
            last_exc = exc
            log.warning("0x0.st upload attempt %d failed: %s", attempt, exc)
            import time
            time.sleep(attempt * 1.5)

    raise RuntimeError(f"Failed to upload to 0x0.st after retries: {last_exc}") from last_exc
