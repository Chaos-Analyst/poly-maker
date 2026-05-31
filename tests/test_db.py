"""Integration tests for poly_utils.db against a live Postgres (docker-compose)."""
import pandas as pd
import pytest
from sqlalchemy import text

from poly_utils import db


@pytest.fixture(autouse=True)
def _clean():
    """Ensure schema exists and tables start empty for each test."""
    db.init_db()
    with db.get_engine().begin() as conn:
        conn.execute(text("DELETE FROM risk_state"))
    yield


def test_init_db_creates_risk_state():
    out = pd.read_sql("SELECT * FROM risk_state LIMIT 0", db.get_engine())
    assert "sleep_till" in out.columns
    assert "time" in out.columns


def test_set_and_get_risk_roundtrip():
    details = {
        "time": "2026-05-31 10:00:00",
        "question": "Will X happen?",
        "msg": "stop loss because spread tight",
        "sleep_till": "2026-05-31 16:00:00",
    }
    db.set_risk("market-abc", details)

    got = db.get_risk("market-abc")
    assert got["time"] == "2026-05-31 10:00:00"
    assert got["question"] == "Will X happen?"
    assert got["msg"] == "stop loss because spread tight"
    assert got["sleep_till"] == "2026-05-31 16:00:00"


def test_get_risk_missing_returns_none():
    assert db.get_risk("does-not-exist") is None


def test_set_risk_upserts():
    db.set_risk("m1", {"sleep_till": "2026-01-01 00:00:00"})
    db.set_risk("m1", {"sleep_till": "2027-01-01 00:00:00"})
    assert db.get_risk("m1")["sleep_till"] == "2027-01-01 00:00:00"


def test_get_risk_sleep_till_is_naive_parseable():
    # trading.py does pd.to_datetime(risk['sleep_till']) and compares to a
    # naive utcnow(); a tz-aware value would raise on comparison.
    db.set_risk("m2", {"sleep_till": "2026-05-31 16:00:00"})
    parsed = pd.to_datetime(db.get_risk("m2")["sleep_till"])
    assert parsed.tzinfo is None


def test_write_and_read_markets_roundtrip():
    df = pd.DataFrame(
        [
            {
                "condition_id": "0xabc",
                "question": "Q1",
                "token1": "111",
                "token2": "222",
                "3_hour": 12.5,
                "volatility_sum": 5.0,
                "best_bid": 0.4,
                "neg_risk": "FALSE",
            }
        ]
    )
    db.write_markets(df)

    out = db.read_markets()
    assert {"condition_id", "question", "token1", "token2", "3_hour", "best_bid"}.issubset(out.columns)
    row = out.iloc[0]
    assert row["condition_id"] == "0xabc"
    assert float(row["3_hour"]) == 12.5
    assert row["neg_risk"] == "FALSE"


def test_write_markets_replaces_existing():
    db.write_markets(pd.DataFrame([{"condition_id": "a", "question": "Qa"}]))
    db.write_markets(
        pd.DataFrame(
            [
                {"condition_id": "b", "question": "Qb"},
                {"condition_id": "c", "question": "Qc"},
            ]
        )
    )
    out = db.read_markets()
    assert len(out) == 2
    assert set(out["condition_id"]) == {"b", "c"}


def test_read_markets_empty_when_no_table():
    # Drop the table to simulate a fresh DB; read should degrade to empty, not raise.
    with db.get_engine().begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS markets"))
    out = db.read_markets()
    assert isinstance(out, pd.DataFrame)
    assert len(out) == 0


def test_write_summary_roundtrip():
    df = pd.DataFrame(
        [
            {
                "question": "Q",
                "answer": "Yes",
                "order_size": 1.0,
                "position_size": 2.0,
                "earnings": 0.5,
                "earning_percentage": 1.2,
            }
        ]
    )
    db.write_summary(df)
    out = pd.read_sql("SELECT * FROM summary", db.get_engine())
    assert len(out) == 1
    assert out.iloc[0]["question"] == "Q"
    assert float(out.iloc[0]["earnings"]) == 0.5
