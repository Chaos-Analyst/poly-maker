"""Single-market trading override (ported from fucked-up-bot/single_market.py).

When ``SINGLE_MARKET_ENABLED=true``, the updater trades **only** the markets listed in
``SINGLE_MARKET_CONDITION_IDS`` (a comma-separated list of 0x-prefixed 64-hex condition
ids), bypassing the sport filter and the reward floor. Used for the occasional "just
trade this one market" override.
"""
import logging
import os

logger = logging.getLogger(__name__)

_CONDITION_ID_HEX_LEN = 64


def _validate_condition_id(raw_id: str) -> str:
    if not raw_id.startswith("0x") or len(raw_id) != _CONDITION_ID_HEX_LEN + 2:
        raise RuntimeError(
            f"SINGLE_MARKET_CONDITION_IDS entry must be a 0x-prefixed 64-hex string, got {raw_id!r}"
        )
    try:
        int(raw_id[2:], 16)
    except ValueError as exc:
        raise RuntimeError(
            f"SINGLE_MARKET_CONDITION_IDS entry is not valid hex: {raw_id!r}"
        ) from exc
    return raw_id.lower()


def load_single_market_restriction():
    """Return a frozenset of lower-cased condition ids, or None if the override is off."""
    raw_enabled = os.environ.get("SINGLE_MARKET_ENABLED", "")
    enabled_norm = raw_enabled.strip().lower()
    if enabled_norm != "true":
        if enabled_norm not in ("", "false"):
            logger.warning(
                "unexpected SINGLE_MARKET_ENABLED value %r, treating as disabled", raw_enabled
            )
        return None

    raw_csv = os.environ.get("SINGLE_MARKET_CONDITION_IDS", "")
    ids = [piece.strip() for piece in raw_csv.split(",") if piece.strip()]
    if not ids:
        raise RuntimeError(
            "SINGLE_MARKET_ENABLED=true but SINGLE_MARKET_CONDITION_IDS is not set"
        )

    return frozenset(_validate_condition_id(raw_id) for raw_id in ids)
