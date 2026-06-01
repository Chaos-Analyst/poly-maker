"""End-to-end: Postgres -> get_market_df -> global_state, the bot's config read path.

Exercises the real data_utils.update_markets() (no Polymarket client needed) against the
live local Postgres, proving DB-backed config flows into global_state exactly as trading.py
consumes it.
"""
import pandas as pd
import pytest

from poly_utils import db

ENV = {
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
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)


def test_update_markets_populates_global_state(env):
    db.write_markets(
        pd.DataFrame(
            [
                {
                    "condition_id": "0xH",
                    "question": "Will H happen?",
                    "answer1": "Yes",
                    "answer2": "No",
                    "token1": "h1",
                    "token2": "h2",
                    "neg_risk": "FALSE",
                    "tick_size": 0.01,
                    "min_size": 15,
                    "max_spread": 3.5,
                    "best_bid": 0.4,
                    "best_ask": 0.6,
                    "3_hour": 12.0,
                    "gm_reward_per_100": 1.0,
                }
            ]
        )
    )

    import poly_data.global_state as gs

    gs.all_tokens = []
    gs.REVERSE_TOKENS = {}
    gs.performing = {}

    from poly_data.data_utils import update_markets

    update_markets()

    # The strategy looks up its market row by condition_id and reads params[param_type].
    assert len(gs.df) == 1
    row = gs.df[gs.df["condition_id"] == "0xH"].iloc[0]
    assert row["trade_size"] == 10.0
    assert row["max_size"] == 30.0
    assert row["param_type"] == "default"
    assert row["3_hour"] == 12.0  # machine column preserved through Postgres

    assert gs.params[row["param_type"]]["stop_loss_threshold"] == -5.0
    assert gs.params["default"]["take_profit_threshold"] == 3.0

    # Token bookkeeping the websocket subscriptions + reverse lookups rely on.
    assert "h1" in gs.all_tokens
    assert gs.REVERSE_TOKENS["h1"] == "h2"
    assert gs.REVERSE_TOKENS["h2"] == "h1"


def test_update_markets_empty_table_does_not_crash(monkeypatch):
    """Fresh database (empty markets table): update_markets() must not crash.

    Regression for the Railway crash-loop. On a brand-new DB the markets table is
    empty, so get_market_df() returns an empty DataFrame, the `len > 0` block is
    skipped, and global_state.df is still its default None. The old code then iterated
    global_state.df.iterrows() unconditionally -> AttributeError, crashing the bot in
    update_once() before main.py's startup wait-loop could run. update_markets() must
    instead leave df unset and return, so the bot waits for the updater's first write.
    """
    import poly_data.global_state as gs
    import poly_data.data_utils as data_utils

    # Simulate the empty markets read on a fresh database.
    monkeypatch.setattr(data_utils, "get_market_df", lambda: (pd.DataFrame(), {}))

    gs.df = None
    gs.all_tokens = []
    gs.REVERSE_TOKENS = {}
    gs.performing = {}

    data_utils.update_markets()  # must not raise

    assert gs.df is None  # nothing to populate yet
    assert gs.all_tokens == []  # no websocket subscriptions built
