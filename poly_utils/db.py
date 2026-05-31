"""Postgres data layer for poly-maker.

Replaces Google Sheets storage. Every process (bot, updater, stats) connects via
DATABASE_URL, so the same code runs whether they share a machine or not. Holds:
  - markets:    machine market catalog (updater writes, bot reads)
  - summary:    account stats snapshot (stats job writes, you read in a GUI)
  - risk_state: per-market stop-loss cooldown (replaces positions/*.json)
"""
import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

_engine = None


def get_engine():
    """Lazily create and cache a SQLAlchemy engine from DATABASE_URL."""
    global _engine
    if _engine is None:
        url = os.getenv("DATABASE_URL")
        if not url:
            raise ValueError("DATABASE_URL environment variable is not set")
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def init_db():
    """Create the risk_state table if it does not exist (idempotent).

    time/sleep_till are TEXT on purpose: trading.py compares sleep_till against a
    naive utcnow(), so we round-trip the exact naive strings it writes rather than
    risk a tz-aware value coming back from a TIMESTAMPTZ column.
    """
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS risk_state (
                    market     TEXT PRIMARY KEY,
                    time       TEXT,
                    question   TEXT,
                    msg        TEXT,
                    sleep_till TEXT,
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
                """
            )
        )


def read_markets():
    """Return the full markets catalog as a DataFrame (empty if not populated yet)."""
    try:
        return pd.read_sql("SELECT * FROM markets", get_engine())
    except Exception:
        return pd.DataFrame()


def write_markets(df):
    """Replace the markets table with df via an atomic staging-table swap.

    Loads into markets_staging, then renames it over markets inside a transaction,
    so concurrent readers (the bot, every 30s) always see either the old or the new
    table -- never a missing one.
    """
    engine = get_engine()
    df.to_sql("markets_staging", engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS markets"))
        conn.execute(text("ALTER TABLE markets_staging RENAME TO markets"))


def write_summary(df):
    """Replace the summary snapshot table (mirrors the old sheet clear()+write)."""
    df.to_sql("summary", get_engine(), if_exists="replace", index=False)


def get_risk(market):
    """Return a market's risk/cooldown row as a dict, or None if absent.

    Mirrors the old positions/{market}.json shape: time/question/msg/sleep_till.
    """
    with get_engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT time, question, msg, sleep_till "
                    "FROM risk_state WHERE market = :m"
                ),
                {"m": str(market)},
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


def set_risk(market, details):
    """Upsert a market's risk/cooldown row from a details dict."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO risk_state (market, time, question, msg, sleep_till, updated_at)
                VALUES (:market, :time, :question, :msg, :sleep_till, now())
                ON CONFLICT (market) DO UPDATE SET
                    time       = EXCLUDED.time,
                    question   = EXCLUDED.question,
                    msg        = EXCLUDED.msg,
                    sleep_till = EXCLUDED.sleep_till,
                    updated_at = now()
                """
            ),
            {
                "market": str(market),
                "time": details.get("time"),
                "question": details.get("question"),
                "msg": details.get("msg"),
                "sleep_till": details.get("sleep_till"),
            },
        )
