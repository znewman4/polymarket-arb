"""Tests for Limitless Exchange HMAC-SHA256 request signing."""

from __future__ import annotations

import base64
import hashlib
import hmac

from polymarket_arb.limitless.signing import sign_request

# A known base64-encoded secret (the one observed in Secrets Manager)
_B64_SECRET = "a77aUecRr6g1cwK6vmRRaLCVnxkj/cfl4STLg7Q0yBM="
_SECRET_BYTES = base64.b64decode(_B64_SECRET)


def _expected_sig(key_bytes: bytes, ts: str, method: str, path: str, body: str) -> str:
    """Recompute the expected base64 HMAC-SHA256 signature."""
    message = f"{ts}\n{method.upper()}\n{path}\n{body}"
    raw = hmac.new(key_bytes, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(raw).decode("utf-8")


class TestSignRequestHeaders:
    def test_returns_required_keys(self) -> None:
        headers = sign_request(
            key_id="kid-1",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            body="{}",
            timestamp="2026-05-27T12:00:00+00:00",
        )
        assert set(headers.keys()) == {"lmts-api-key", "lmts-timestamp", "lmts-signature"}

    def test_key_id_passthrough(self) -> None:
        headers = sign_request(
            key_id="my-key-id",
            key_secret=_B64_SECRET,
            method="GET",
            path="/markets",
            body="",
            timestamp="2026-05-27T12:00:00+00:00",
        )
        assert headers["lmts-api-key"] == "my-key-id"

    def test_timestamp_passthrough(self) -> None:
        ts = "2026-05-27T10:30:00+00:00"
        headers = sign_request(
            key_id="k",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            body="{}",
            timestamp=ts,
        )
        assert headers["lmts-timestamp"] == ts

    def test_auto_timestamp_is_iso8601(self) -> None:
        headers = sign_request(
            key_id="k",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            body="{}",
        )
        ts = headers["lmts-timestamp"]
        # Must contain a date separator and time separator
        assert "T" in ts
        assert "-" in ts

    def test_signature_is_base64(self) -> None:
        headers = sign_request(
            key_id="k",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            body='{"amount":"10"}',
            timestamp="2026-05-27T12:00:00+00:00",
        )
        sig = headers["lmts-signature"]
        # Must be valid base64 (no exception)
        decoded = base64.b64decode(sig)
        assert len(decoded) == 32  # SHA-256 → 32 bytes


class TestSignRequestCorrectness:
    def test_known_vector_post(self) -> None:
        ts = "2026-05-27T12:00:00.000000+00:00"
        body = '{"amount":"10.000000","marketAddress":"0xabc","outcome":"YES","price":"0.600000","side":"BUY"}'
        expected = _expected_sig(_SECRET_BYTES, ts, "POST", "/orders", body)

        headers = sign_request(
            key_id="kid",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            body=body,
            timestamp=ts,
        )
        assert headers["lmts-signature"] == expected

    def test_known_vector_get_empty_body(self) -> None:
        ts = "2026-05-27T09:00:00.000000+00:00"
        expected = _expected_sig(_SECRET_BYTES, ts, "GET", "/markets", "")

        headers = sign_request(
            key_id="kid",
            key_secret=_B64_SECRET,
            method="GET",
            path="/markets",
            body="",
            timestamp=ts,
        )
        assert headers["lmts-signature"] == expected

    def test_different_body_gives_different_sig(self) -> None:
        kwargs = dict(
            key_id="k",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            timestamp="2026-05-27T12:00:00+00:00",
        )
        h1 = sign_request(**kwargs, body='{"amount":"10"}')
        h2 = sign_request(**kwargs, body='{"amount":"20"}')
        assert h1["lmts-signature"] != h2["lmts-signature"]

    def test_method_case_insensitive(self) -> None:
        """Lower-case method should produce same sig as upper-case."""
        ts = "2026-05-27T12:00:00+00:00"
        h_upper = sign_request(key_id="k", key_secret=_B64_SECRET, method="POST", path="/orders", body="{}", timestamp=ts)
        h_lower = sign_request(key_id="k", key_secret=_B64_SECRET, method="post", path="/orders", body="{}", timestamp=ts)
        assert h_upper["lmts-signature"] == h_lower["lmts-signature"]


class TestSecretDecoding:
    def test_b64_secret_decoded_to_bytes(self) -> None:
        """Signature with b64-encoded secret must equal manually b64-decoded computation."""
        ts = "2026-05-27T12:00:00+00:00"
        body = '{"x":1}'
        expected = _expected_sig(_SECRET_BYTES, ts, "POST", "/orders", body)
        headers = sign_request(
            key_id="k",
            key_secret=_B64_SECRET,
            method="POST",
            path="/orders",
            body=body,
            timestamp=ts,
        )
        assert headers["lmts-signature"] == expected

    def test_raw_utf8_secret_fallback(self) -> None:
        """If the secret is not valid base64, it falls back to raw UTF-8 encoding."""
        raw_secret = "not-base64!!!"
        ts = "2026-05-27T12:00:00+00:00"
        body = "{}"
        # Manually compute with raw UTF-8
        message = f"{ts}\nPOST\n/orders\n{body}"
        raw = hmac.new(raw_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        expected = base64.b64encode(raw).decode("utf-8")

        headers = sign_request(
            key_id="k",
            key_secret=raw_secret,
            method="POST",
            path="/orders",
            body=body,
            timestamp=ts,
        )
        assert headers["lmts-signature"] == expected

    def test_b64_and_raw_utf8_produce_different_sigs(self) -> None:
        """b64-decoded key vs raw UTF-8 key should give different signatures."""
        ts = "2026-05-27T12:00:00+00:00"
        h_b64 = sign_request(key_id="k", key_secret=_B64_SECRET, method="POST", path="/orders", body="{}", timestamp=ts)
        # Build the raw-UTF8 version manually
        message = f"{ts}\nPOST\n/orders\n{{}}"
        raw_sig = hmac.new(_B64_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        raw_b64_sig = base64.b64encode(raw_sig).decode("utf-8")
        assert h_b64["lmts-signature"] != raw_b64_sig
