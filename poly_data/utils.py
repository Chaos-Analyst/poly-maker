import json
import os

import pandas as pd  # noqa: F401  (kept for callers importing pd from here)

from poly_utils import db


def pretty_print(txt, dic):
    print("\n", txt, json.dumps(dic, indent=4))


def _required_float(name):
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        raise ValueError(
            f"Required env var {name} is not set. Copy it into your .env (see .env.example)."
        )
    return float(val)


def _optional_float(name):
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return None
    return float(val)


def get_params_from_env():
    """The five strategy hyperparameters as one global set (was the Hyperparameters sheet).

    Returned under a 'default' key so trading.py's ``params[row['param_type']]``
    keeps working unchanged -- every market is tagged param_type='default'.
    """
    return {
        "default": {
            "stop_loss_threshold": _required_float("STOP_LOSS_THRESHOLD"),
            "spread_threshold": _required_float("SPREAD_THRESHOLD"),
            "volatility_threshold": _required_float("VOLATILITY_THRESHOLD"),
            "sleep_period": _required_float("SLEEP_PERIOD"),
            "take_profit_threshold": _required_float("TAKE_PROFIT_THRESHOLD"),
        }
    }


def get_market_df():
    """Load the markets catalog from Postgres and global config from .env.

    Returns ``(df, params)`` with the same shape the old Sheets reader returned, so
    data_utils/trading consume it unchanged. The per-market sizing/grouping fields
    that used to live in the Selected Markets sheet (trade_size, max_size,
    multiplier, param_type) are injected here as global, .env-driven values.
    """
    params = get_params_from_env()

    trade_size = _required_float("TRADE_SIZE")
    max_size = _optional_float("MAX_SIZE")
    multiplier = os.getenv("MULTIPLIER", "") or ""

    df = db.read_markets()
    if len(df) > 0 and "question" in df.columns:
        df = df[df["question"] != ""].reset_index(drop=True)

    df["trade_size"] = trade_size
    df["max_size"] = max_size if max_size is not None else trade_size
    df["multiplier"] = multiplier
    df["param_type"] = "default"

    return df, params
