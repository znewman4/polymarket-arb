from __future__ import annotations

import pytest

from polymarket_arb.compliance.trade_gate import OrdersForbidden, raise_if_orders_disallowed


def test_default_is_forbidden(settings):
    assert settings.orders_allowed is False
    with pytest.raises(OrdersForbidden):
        raise_if_orders_disallowed(settings)


def test_allows_when_flag_true(settings):
    settings.orders_allowed = True
    raise_if_orders_disallowed(settings)
