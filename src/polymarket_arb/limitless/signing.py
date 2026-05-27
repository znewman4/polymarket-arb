"""HMAC-SHA256 request signing for the Limitless Exchange private API.

Limitless requires three headers on authenticated requests:
  lmts-api-key       — your API key identifier
  lmts-timestamp     — ISO-8601 UTC timestamp
  lmts-signature     — Base64-encoded HMAC-SHA256(key_secret_bytes, canonical_message)

Canonical message (newline-separated):
  {ISO-8601 timestamp}
  {HTTP METHOD}
  {request path with query string}
  {request body}

Credentials are stored in AWS Secrets Manager as "limitless/api_credentials"
with keys "key_id" and "key_secret".  key_secret is base64-encoded; it is
decoded to raw bytes before use as the HMAC key.  In paper_mode these
functions are never called; the LimitlessOrderClient short-circuits before
reaching this module.

Reference: https://docs.limitless.exchange/developers/authentication
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone


def sign_request(
    *,
    key_id: str,
    key_secret: str,
    method: str,
    path: str,
    body: str,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Return the three Limitless auth headers for a signed request.

    Args:
        key_id:     API key identifier from Secrets Manager.
        key_secret: API key secret from Secrets Manager (base64-encoded).
        method:     HTTP method in uppercase (e.g. "POST", "GET").
        path:       Request path including any query string (e.g. "/orders").
        body:       Raw request body string (JSON). Pass "" for GET requests.
        timestamp:  Override ISO-8601 UTC timestamp string. Uses current UTC
                    time if None.

    Returns:
        Dict with keys lmts-api-key, lmts-timestamp, lmts-signature.
    """
    ts = timestamp if timestamp is not None else datetime.now(timezone.utc).isoformat()

    # Canonical message: timestamp + method + path + body, newline-separated
    message = f"{ts}\n{method.upper()}\n{path}\n{body}"

    # key_secret is base64-encoded in Secrets Manager; decode to raw bytes
    try:
        key_bytes = base64.b64decode(key_secret)
    except Exception:
        key_bytes = key_secret.encode("utf-8")

    # Signature is base64-encoded (not hex)
    raw_sig = hmac.new(
        key_bytes,
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sig = base64.b64encode(raw_sig).decode("utf-8")

    return {
        "lmts-api-key": key_id,
        "lmts-timestamp": ts,
        "lmts-signature": sig,
    }
