"""Tests for delta-neutral (build-both-sides, never-sell) logic:
the imbalance guard, balance-error detection, and the mergeable-pair scan."""
import pandas as pd

import poly_data.global_state as global_state
from poly_data import trading_utils
from poly_data.polymarket_client import PolymarketClient


# ---- imbalance guard (keep sides balanced) ----

def test_delta_neutral_buys_a_lot_when_within_tolerance():
    row = {"trade_size": 10}
    assert trading_utils.delta_neutral_buy_amount(0, 0, row, 10) == 10      # both empty
    assert trading_utils.delta_neutral_buy_amount(10, 10, row, 10) == 10    # balanced
    assert trading_utils.delta_neutral_buy_amount(20, 10, row, 10) == 10    # exactly at tolerance edge


def test_delta_neutral_pauses_the_leading_side():
    row = {"trade_size": 10}
    # 21 > 10 + 10 -> this side is too far ahead, pause it so the other can catch up
    assert trading_utils.delta_neutral_buy_amount(21, 10, row, 10) == 0
    # lagging side always buys
    assert trading_utils.delta_neutral_buy_amount(10, 30, row, 10) == 10


# ---- balance/allowance error detection ----

def test_is_balance_error_detects_balance_and_allowance():
    assert PolymarketClient.is_balance_error({"errorMsg": "not enough balance / allowance"})
    assert PolymarketClient.is_balance_error({"error": "insufficient allowance"})
    assert PolymarketClient.is_balance_error(
        {"success": False, "errorMsg": "PolyApiException[status_code=400, ... not enough balance ...]"}
    )


def test_is_balance_error_false_otherwise():
    assert not PolymarketClient.is_balance_error({"success": True, "orderID": "0x1"})
    assert not PolymarketClient.is_balance_error({"errorMsg": "tick size mismatch"})
    assert not PolymarketClient.is_balance_error({})
    assert not PolymarketClient.is_balance_error(None)
    assert not PolymarketClient.is_balance_error("oops")


# ---- mergeable-pair scan ----

class _FakeClient:
    def __init__(self, posdf, raws):
        # posdf: data-api positions DataFrame; raws: {token_id: raw on-chain balance}
        self._posdf = posdf
        self._raws = raws
        self.merges = []

    def get_all_positions(self):
        return self._posdf

    def get_position(self, token):
        raw = self._raws.get(str(token), 0)
        shares = raw / 1e6
        return raw, (0 if shares < 1 else shares)

    def merge_positions(self, amount, condition_id, is_neg_risk):
        self.merges.append((amount, condition_id, is_neg_risk))
        return "0xhash"


def test_merge_mergeable_pairs_merges_every_full_pair_from_all_positions(monkeypatch):
    import trading
    # Note: global_state.df is intentionally NOT set -- merging now scans ALL wallet positions
    # (via get_all_positions), not just the current trading universe.
    posdf = pd.DataFrame([
        {"conditionId": "0xA", "asset": "1", "size": 30.0, "negativeRisk": False, "title": "A"},
        {"conditionId": "0xA", "asset": "2", "size": 20.0, "negativeRisk": False, "title": "A"},  # both sides -> merge
        {"conditionId": "0xB", "asset": "3", "size": 10.0, "negativeRisk": False, "title": "B"},  # one side -> skip
    ])
    fake = _FakeClient(posdf, raws={"1": 30_000000, "2": 20_000000, "3": 10_000000})
    monkeypatch.setattr(global_state, "client", fake)
    monkeypatch.setattr(global_state, "positions", {})

    merged = trading.merge_mergeable_pairs()

    assert merged is True
    # only 0xA had both sides; merged min(30e6, 20e6), non-neg-risk
    assert fake.merges == [(20_000000, "0xA", False)]


def test_merge_mergeable_pairs_noop_when_nothing_to_merge(monkeypatch):
    import trading
    posdf = pd.DataFrame([
        {"conditionId": "0xA", "asset": "1", "size": 10.0, "negativeRisk": False, "title": "A"},  # one side only
    ])
    fake = _FakeClient(posdf, raws={"1": 10_000000})
    monkeypatch.setattr(global_state, "client", fake)
    monkeypatch.setattr(global_state, "positions", {})

    assert trading.merge_mergeable_pairs() is False
    assert fake.merges == []
