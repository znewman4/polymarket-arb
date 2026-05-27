# Task Change Log - 2026-05-27

## Limitless Live Submit Error Diagnostics

### Change - Log API response body on failed live order submission

Changed files:

- `src/polymarket_arb/limitless/order_client.py`
  - In the `_live_submit()` `HttpError` handler, extracts
    `getattr(getattr(exc, "response", None), "text", "")`.
  - The error log now includes the HTTP response body alongside the market
    slug and exception message.
- `src/polymarket_arb/http/client.py`
  - Extended `HttpError` to retain an optional `httpx.Response`.
  - When a non-transient HTTP status failure is wrapped, the underlying
    response is attached to `HttpError`; without this support, the new
    Limitless log field would be empty for production API failures.
- `tests/test_limitless/test_order_client.py`
  - Added a failed live-submit test verifying that an API body such as
    `{"error":"invalid signature"}` is included in the logged error.
- `tests/test_http/test_client_retry.py`
  - Added a regression assertion that non-transient HTTP failures retain their
    response text on the raised `HttpError`.

Result: failed Limitless live submissions now expose the exchange's diagnostic
response body in service logs, making authentication and payload errors
actionable during deployment troubleshooting.

Verification:

- `python -m pytest tests/ -q`: `913 passed`
- `python -m ruff check src/ tests/`: passed
- `git diff --check`: passed
