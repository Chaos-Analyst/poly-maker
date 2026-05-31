"""Tests for the stats writer after migrating off Sheets (no Selected Markets)."""
import pandas as pd

from poly_utils import db
from poly_stats import account_stats


def test_get_markets_df_reads_from_db():
    db.write_markets(
        pd.DataFrame(
            [
                {
                    "condition_id": "0x1",
                    "question": "Q",
                    "answer1": "Y",
                    "answer2": "N",
                    "token1": 111,
                    "token2": 222,
                    "extra": "ignored",
                }
            ]
        )
    )
    out = account_stats.get_markets_df()
    assert list(out.columns) == ["question", "answer1", "answer2", "token1", "token2"]
    assert out.iloc[0]["token1"] == "111"  # cast to str


def test_combine_dfs_drops_marketInSelected_and_needs_no_selected_df():
    orders = pd.DataFrame(
        [{"asset_id": "t1", "order_size": 5.0, "order_side": "BUY", "order_price": 0.4}]
    )
    positions = pd.DataFrame(
        [{"asset": "t1", "position_size": 10.0, "avgPrice": 0.4, "curPrice": 0.5, "percentPnl": 1.0}]
    )
    markets = pd.DataFrame(
        [{"question": "Q", "answer1": "Yes", "answer2": "No", "token1": "t1", "token2": "t2"}]
    )
    out = account_stats.combine_dfs(orders, positions, markets)
    assert "marketInSelected" not in out.columns
    assert out.iloc[0]["question"] == "Q"
    assert out.iloc[0]["answer"] == "Yes"  # mapped via token1
