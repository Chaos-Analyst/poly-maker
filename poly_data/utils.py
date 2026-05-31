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


def _optional_bool(name, default):
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


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
            # Behavior switches; defaults preserve the original market-making behavior.
            # ENABLE_SELLING=false  -> never place a market SELL (no stop-loss / take-profit).
            # BUILD_BOTH_SIDES=true -> accumulate YES and NO and merge them (delta-neutral).
            "enable_selling": _optional_bool("ENABLE_SELLING", True),
            "build_both_sides": _optional_bool("BUILD_BOTH_SIDES", False),
            # Delta-neutral knobs (only used when build_both_sides is on):
            # MAX_IMBALANCE         -> max shares one side may lead the other before we pause
            #                          buying the leading side (blank = fall back to trade_size).
            # MERGE_COLLATERAL_FLOOR-> proactively merge mergeable pairs when free collateral
            #                          drops below this (blank = only merge on a balance error).
            "max_imbalance": _optional_float("MAX_IMBALANCE"),
            "merge_collateral_floor": _optional_float("MERGE_COLLATERAL_FLOOR"),
            # POST_ONLY=true (default): buy orders are maker-only -- if one would cross and fill as a
            # taker it's rejected instead, keeping every fill reward-eligible. Applies to buys only;
            # sells (e.g. a stop-loss) still execute normally.
            "post_only": _optional_bool("POST_ONLY", True),
        }
    }


def get_market_df():
    """Load the markets catalog from Postgres and global config from .env.

    Returns ``(df, params)`` with the same shape the old Sheets reader returned, so
    data_utils/trading consume it unchanged. The per-market sizing/grouping fields
    that used to live in the Selected Markets sheet (trade_size, max_size,
    multiplier, param_type) are injected here from .env.

    ``TRADE_SIZE`` is optional: leave it blank to size each market at its own
    ``min_size`` (the smallest reward-qualifying order). Set it to force one fixed size
    on every market and **ignore** each market's reward ``min_size`` -- the bot will then
    place orders even below the reward threshold (good for plain trading; those orders
    just won't earn rewards). ``MAX_SIZE`` blank means "same as trade_size".
    """
    params = get_params_from_env()

    trade_size = _optional_float("TRADE_SIZE")
    max_size = _optional_float("MAX_SIZE")
    multiplier = os.getenv("MULTIPLIER", "") or ""

    df = db.read_markets()
    if len(df) > 0 and "question" in df.columns:
        df = df[df["question"] != ""].reset_index(drop=True)

    # Order size per market: a fixed TRADE_SIZE if set, else each market's own min_size.
    if trade_size is not None:
        df["trade_size"] = trade_size
        # Explicit TRADE_SIZE: trade exactly this size and IGNORE each market's reward
        # min_size. trading.py gates buys on row['min_size'] (buy_amount >= min_size) and
        # uses it for sizing heuristics, so override min_size to trade_size to honor it.
        df["min_size"] = trade_size
    elif "min_size" in df.columns:
        df["trade_size"] = pd.to_numeric(df["min_size"], errors="coerce")
    else:
        df["trade_size"] = float("nan")

    # Max inventory per side before it only sells; MAX_SIZE if set, else == trade_size.
    df["max_size"] = max_size if max_size is not None else df["trade_size"]
    df["multiplier"] = multiplier
    df["param_type"] = "default"

    return df, params
