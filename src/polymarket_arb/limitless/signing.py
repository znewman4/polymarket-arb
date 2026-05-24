"""HMAC-SHA256 request signing for the Limitless Exchange private API.

Limitless requires three headers on authenticated requests:
  lmts-api-key       — your API key identifier
  lmts-timestamp     — Unix timestamp in milliseconds (str)
  lmts-signature     — HMAC-SHA256(key_secret, timestamp_ms + body)

Credentials are stored in AWS Secrets Manager as "limitless/api_credentials"
with keys "key_id" and "key_secret".  In paper_mode these functions are never
called; the LimitlessOrderClient short-circuits before reaching this module.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign_request(
    *,
    key_id: str,
    key_secret: str,
    body: str,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    """Return the three Limitless auth headers for a signed request.

    Args:
        key_id:       API key identifier from Secrets Manager.
        key_secret:   API key secret from Secrets Manager.
        body:         Raw request body string (JSON). Pass "" for GET requests.
        timestamp_ms: Override timestamp (milliseconds). Uses current time if None.

    Returns:
        Dict with lmts-api-key, lmts-timestamp, lmts-signature.
    """
    ts_ms = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    message = str(ts_ms) + body
    sig = hmac.new(
        key_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "lmts-api-key": key_id,
        "lmts-timestamp": str(ts_ms),
        "lmts-signature": sig,
    }
