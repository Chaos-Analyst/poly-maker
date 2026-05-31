"""Tests for the updater's market selection now that the human allow-list is gone."""
import pandas as pd

from data_updater.find_markets import get_markets


def _market(**kw):
    base = dict(
        question="Q",
        answer1="Yes",
        answer2="No",
        neg_risk="FALSE",
        best_bid=0.4,
        best_ask=0.6,
        rewards_daily_rate=1.0,
        bid_reward_per_100=1.0,
        ask_reward_per_100=1.0,
        gm_reward_per_100=1.0,
        sm_reward_per_100=1.0,
        min_size=15,
        max_spread=3.5,
        tick_size=0.01,
        market_slug="slug",
        token1="1",
        token2="2",
        condition_id="0x1",
    )
    base.update(kw)
    return base


def test_get_markets_no_selection_keeps_reward_bearing_drops_low():
    results = [
        _market(question="High", gm_reward_per_100=1.0, condition_id="0xH", token1="h1", token2="h2"),
        _market(question="Low", gm_reward_per_100=0.1, condition_id="0xL", token1="l1", token2="l2"),
    ]
    # No sel_df argument: with the human allow-list removed, the updater must run.
    all_data, all_markets = get_markets(results, maker_reward=0.75)

    qs = set(all_markets["question"])
    assert "High" in qs
    assert "Low" not in qs
    # all_data is still the full discovered set (pre reward filter).
    assert set(all_data["question"]) == {"High", "Low"}


def test_get_markets_empty_sel_df_does_not_raise():
    results = [_market(question="A", condition_id="0xA", token1="a1", token2="a2")]
    _, all_markets = get_markets(results, sel_df=pd.DataFrame(), maker_reward=0.75)
    assert "A" in set(all_markets["question"])
