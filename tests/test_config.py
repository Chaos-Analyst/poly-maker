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


def test_max_size_defaults_to_trade_size_when_blank(seed_markets, monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("MAX_SIZE", "")
    df, _ = utils.get_market_df()
    assert df.iloc[0]["max_size"] == 10.0


def test_missing_required_env_raises_clear_error(seed_markets, monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TRADE_SIZE", raising=False)
    with pytest.raises(ValueError, match="TRADE_SIZE"):
        utils.get_market_df()
