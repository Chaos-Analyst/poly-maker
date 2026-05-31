"""Tests for the env-backed market/config loader (replaces the Sheets read)."""
import pandas as pd
import pytest

from poly_utils import db
from poly_data import utils

REQUIRED_ENV = {
    "STOP_LOSS_THRESHOLD": "-5",
    "SPREAD_THRESHOLD": "0.02",
    "VOLATILITY_THRESHOLD": "200",
    "SLEEP_PERIOD": "6",
    "TAKE_PROFIT_THRESHOLD": "3",
    "TRADE_SIZE": "10",
    "MAX_SIZE": "30",
    "MULTIPLIER": "",
}


@pytest.fixture
def seed_markets():
    db.write_markets(
        pd.DataFrame(
            [
                {
                    "condition_id": "0x1",
                    "question": "Q1",
                    "token1": "1",
                    "token2": "2",
                    "3_hour": 5.0,
                    "neg_risk": "FALSE",
                    "tick_size": 0.01,
                    "min_size": 15,
                    "max_spread": 3.5,
                    "best_bid": 0.4,
                    "best_ask": 0.6,
                    "answer1": "Yes",
                    "answer2": "No",
                },
                {"condition_id": "0x2", "question": "", "token1": "3", "token2": "4"},
            ]
        )
    )


@pytest.fixture
def full_env(monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)


def test_drops_blank_question_rows(seed_markets, full_env):
    df, _ = utils.get_market_df()
    assert list(df["question"]) == ["Q1"]


def test_injects_sizing_and_param_type(seed_markets, full_env):
    df, _ = utils.get_market_df()
    row = df.iloc[0]
    assert row["trade_size"] == 10.0
    assert row["max_size"] == 30.0
    assert row["multiplier"] == ""
    assert row["param_type"] == "default"


def test_params_built_from_env(seed_markets, full_env):
    _, params = utils.get_market_df()
    p = params["default"]
    assert p["stop_loss_threshold"] == -5.0
    assert p["spread_threshold"] == 0.02
    assert p["volatility_threshold"] == 200.0
    assert p["sleep_period"] == 6.0
    assert p["take_profit_threshold"] == 3.0


def test_selling_and_both_sides_default_to_legacy_behavior(seed_markets, full_env, monkeypatch):
    # Unset -> preserve original behavior: selling on, build-both-sides off.
    monkeypatch.delenv("ENABLE_SELLING", raising=False)
    monkeypatch.delenv("BUILD_BOTH_SIDES", raising=False)
    _, params = utils.get_market_df()
    p = params["default"]
    assert p["enable_selling"] is True
    assert p["build_both_sides"] is False


def test_selling_and_both_sides_read_from_env(seed_markets, full_env, monkeypatch):
    monkeypatch.setenv("ENABLE_SELLING", "false")
    monkeypatch.setenv("BUILD_BOTH_SIDES", "true")
    _, params = utils.get_market_df()
    p = params["default"]
    assert p["enable_selling"] is False
    assert p["build_both_sides"] is True


def test_delta_neutral_knobs_default_none(seed_markets, full_env, monkeypatch):
    monkeypatch.delenv("MAX_IMBALANCE", raising=False)
    monkeypatch.delenv("MERGE_COLLATERAL_FLOOR", raising=False)
    _, params = utils.get_market_df()
    p = params["default"]
    assert p["max_imbalance"] is None
    assert p["merge_collateral_floor"] is None


def test_delta_neutral_knobs_read_from_env(seed_markets, full_env, monkeypatch):
    monkeypatch.setenv("MAX_IMBALANCE", "25")
    monkeypatch.setenv("MERGE_COLLATERAL_FLOOR", "12.5")
    _, params = utils.get_market_df()
    p = params["default"]
    assert p["max_imbalance"] == 25.0
    assert p["merge_collateral_floor"] == 12.5


def test_max_size_defaults_to_trade_size_when_blank(seed_markets, monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MAX_SIZE", "")
    df, _ = utils.get_market_df()
    assert df.iloc[0]["max_size"] == 10.0


def test_missing_required_hyperparam_raises_clear_error(seed_markets, monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("STOP_LOSS_THRESHOLD", raising=False)
    with pytest.raises(ValueError, match="STOP_LOSS_THRESHOLD"):
        utils.get_market_df()


def test_trade_size_unset_uses_per_market_min_size(seed_markets, monkeypatch):
    # TRADE_SIZE and MAX_SIZE blank -> size each market at its own min_size.
    for k in (
        "STOP_LOSS_THRESHOLD",
        "SPREAD_THRESHOLD",
        "VOLATILITY_THRESHOLD",
        "SLEEP_PERIOD",
        "TAKE_PROFIT_THRESHOLD",
    ):
        monkeypatch.setenv(k, REQUIRED_ENV[k])
    monkeypatch.delenv("TRADE_SIZE", raising=False)
    monkeypatch.delenv("MAX_SIZE", raising=False)
    monkeypatch.setenv("MULTIPLIER", "")

    df, _ = utils.get_market_df()
    row = df.iloc[0]
    assert row["trade_size"] == 15.0  # the seeded market's min_size
    assert row["max_size"] == 15.0  # defaults to trade_size
    assert row["min_size"] == 15.0  # blank TRADE_SIZE leaves the real min_size in place


def test_fixed_trade_size_overrides_min_size(seed_markets, full_env):
    # Seeded market has min_size=15; with TRADE_SIZE=10 the bot must ignore 15 and use 10,
    # so the buy gate (buy_amount >= min_size) lets a 10-share order through.
    df, _ = utils.get_market_df()
    row = df.iloc[0]
    assert row["trade_size"] == 10.0
    assert row["min_size"] == 10.0  # overridden to trade_size
