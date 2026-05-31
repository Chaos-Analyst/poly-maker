# Configuration & storage

This bot used to keep everything in one Google Spreadsheet. It now uses **Postgres**
for machine data and **`.env`** for configuration. Google Sheets has been removed.

This document records **what used to be hand-edited**, **what each setting means**, and
**how to re-add the human controls later**.

---

## Where things live now

| Data | Old home (Sheets tab) | New home |
|------|-----------------------|----------|
| Market catalog (questions, tokens, volatility, rewards, tick size…) | All / Full / Volatility Markets | Postgres table **`markets`** |
| Account snapshot (orders, positions, earnings) | Summary | Postgres table **`summary`** |
| Stop-loss cooldown per market | local `positions/*.json` | Postgres table **`risk_state`** |
| Strategy hyperparameters | Hyperparameters | **`.env`** (5 vars) |
| Position sizing | Selected Markets (per row) | **`.env`** (3 vars) |
| Which markets to trade | Selected Markets (hand-picked) | **Sports-only** — today/live games chosen by the updater (see below) |

All three processes — the bot (`main.py`), the updater (`update_markets.py`), and the
stats job (`update_stats.py`) — read their database connection from `DATABASE_URL`, so
they can run on the same machine or different ones; just point each at the DB.

---

## What used to be hand-edited

Two spreadsheet tabs were typed in by a human. Together they fed **8 fields** into the
strategy.

### A. *Selected Markets* — one row per market a human chose to trade
| Field | Required | Read at | Meaning |
|-------|----------|---------|---------|
| `question` | yes | join key | market identity (joined to the catalog) — **no longer used** |
| `param_type` | yes | `trading.py` | which hyperparameter group to apply — **no longer used** (one global set) |
| `trade_size` | **yes** | `trading_utils.py` | order-size increment per quote |
| `max_size` | no (defaults to `trade_size`) | `trading_utils.py` | max position per side before it only sells |
| `multiplier` | no | `trading_utils.py` | multiplies buy size for assets priced < 0.10 |

### B. *Hyperparameters* — `type / param / value` rows (5 knobs)
| Param | Read at | Meaning |
|-------|---------|---------|
| `stop_loss_threshold` | `trading.py` | exit if PnL % drops below this (and the spread is tight enough) |
| `spread_threshold` | `trading.py` | max bid/ask spread (price units) at which a stop-loss exit is allowed |
| `volatility_threshold` | `trading.py` | 3-hour annualized volatility above which we stop buying / risk off |
| `sleep_period` | `trading.py` | hours to pause buying after a stop-loss |
| `take_profit_threshold` | `trading.py` | profit % target above average cost |

Everything else the strategy uses (`tick_size`, `min_size`, `max_spread`, `neg_risk`,
`3_hour`, `best_bid/ask`, `token1/2`, `condition_id`, `answer1/2`, volatility columns) is
**machine-generated** by the updater into the `markets` table — never hand-typed.

---

## The `.env` settings (replacing the two tabs)

```ini
# Strategy hyperparameters (was the Hyperparameters tab) — one global set.
STOP_LOSS_THRESHOLD=-5
SPREAD_THRESHOLD=0.02
VOLATILITY_THRESHOLD=200
SLEEP_PERIOD=6
TAKE_PROFIT_THRESHOLD=3

# Position sizing. TRADE_SIZE blank -> each market's own min_size; set -> fixed everywhere.
# MAX_SIZE blank -> same as trade_size (hold at most one lot per side).
TRADE_SIZE=
MAX_SIZE=
MULTIPLIER=          # blank disables the low-price multiplier
```

Loaded in `poly_data/utils.py:get_market_df()`, which reads the `markets` table and
injects `trade_size` / `max_size` / `multiplier` / `param_type='default'` as columns so
the strategy code (`trading.py`, `trading_utils.py`) is unchanged. Missing a required
*hyperparameter* raises a clear error at startup rather than failing mid-trade.

`TRADE_SIZE` is **optional**:

- **Blank** → each market is sized at its own `min_size` (the smallest reward-qualifying
  order). Because `trading.py` gates buys on `buy_amount >= row['min_size']`, this is also
  the smallest size the bot will place — so blank means "post each market's minimum."
- **Set to a number** → that fixed size on every market, and each market's `min_size` is
  overridden to it (in `get_market_df`), so the `buy_amount >= min_size` gate uses your
  size. This lets the bot place orders **below** a market's reward threshold — fine for
  plain trading, but such orders earn no rewards. Keep it large enough that
  `TRADE_SIZE × price ≥ $1` (Polymarket's minimum order; the bot only quotes prices
  0.1–0.9, so ~10+ shares stays safe).

`MAX_SIZE` blank means "same as `trade_size`" (hold at most one lot per side).

> The Hyperparameters tab supported several `type` groups (e.g. aggressive/conservative).
> That grouping is **collapsed to one global set**. When migrating, pick the set you want
> (the conservative one is the safe default) and copy those numbers in.

---

## Which markets get traded

The bot trades **every market the updater writes to the `markets` table**. The updater
restricts that table to **today/live sport game markets** that also pay maker rewards — two
stages joined on `condition_id`:

1. **Sport membership** (`data_updater/sports.py`): the Polymarket **Gamma API**
   (`/events?tag_slug=…`) is queried per enabled sport and filtered to individual *games*:
   - **soccer** — moneyline / totals / both-teams-to-score (event title without "winner")
   - **tennis** — singles moneyline (dated slug, "doubles" excluded)
   - **nba / nhl / mlb** — moneyline / totals
   - **esports** — CS2 + LoL moneyline (slug starting `cs2-` / `lol-`)

   A market is in-universe only if its event has a **dated slug** (`YYYY-MM-DD` — which excludes
   season-long futures like `world-cup-winner` / `2026-nba-champion`) and is **live now or
   resolves within `SPORTS_HORIZON_HOURS`** (default 36). Ended games drop out automatically
   because they leave the reward feed.
2. **Reward floor** (existing logic): of those, keep markets with
   `gm_reward_per_100 >= MAKER_REWARD` (default `0.75`). Reward/volatility columns are computed
   exactly as before; only the input set is smaller.

| Env var | Default | Meaning |
|---------|---------|---------|
| `SPORTS_TAG_SLUGS` | all six | Comma-separated subset of `soccer,tennis,nba,nhl,mlb,esports` to enable |
| `SPORTS_HORIZON_HOURS` | `36` | How far ahead a game may resolve and still count as "today" |
| `MAKER_REWARD` | `0.75` | Reward floor (`gm_reward_per_100`) |
| `UPDATE_INTERVAL_SECONDS` | `900` | Updater refresh cadence (was a hard-coded hour) |
| `MIN_MARKETS_TO_WRITE` | `1` | Anti-wipe: skip the write if fewer markets pass |

The `markets` table gains a `sport` column (the tag that selected each row); `trading.py`
ignores it.

### Single-market override

Set `SINGLE_MARKET_ENABLED=true` and `SINGLE_MARKET_CONDITION_IDS=0x…,0x…` to trade **only**
those markets, bypassing the sport filter **and** the reward floor (see
`data_updater/single_market.py`). Only ids present in the CLOB reward feed are picked up; any
others are logged and skipped.

⚠️ **Exposure:** `TRADE_SIZE` applies to *every* market in the table simultaneously. Total
capital at risk ≈ `TRADE_SIZE × (number of markets in the table)`. Start small.

---

## Postgres tables

- **`markets`** — full catalog; the updater replaces it each cycle via an atomic
  staging-table swap (readers always see a complete table). Column names match the old
  sheet exactly (including `3_hour`, `volatility_sum`).
- **`summary`** — account snapshot; the stats job replaces it each cycle. (The old
  `marketInSelected` column was dropped along with selection.)
- **`risk_state`** — `market` (PK), `time`, `question`, `msg`, `sleep_till`, `updated_at`.
  `time`/`sleep_till` are stored as TEXT to preserve the naive-timestamp semantics the
  cooldown comparison in `trading.py` relies on.

Edit/seed config with any Postgres GUI (TablePlus / DBeaver / pgAdmin) or `psql`.

---

## How to re-add human market selection later

The hooks are intentionally small:

1. Add a `selected_markets` table (`question` + optional per-market `trade_size`,
   `max_size`, `multiplier`, `param_type`).
2. In `poly_data/utils.py:get_market_df()`, inner-join `markets` against `selected_markets`
   on `question` instead of returning all rows, and use per-row overrides where present
   (falling back to the `.env` defaults).
3. In `data_updater/find_markets.py:get_markets`, pass the selected set as `sel_df` again
   (the function already accepts it) so the updater retains chosen markets.
4. Optionally restore the per-`type` hyperparameter grouping by returning multiple groups
   from `get_params_from_env()` (or a `hyperparameters` table) keyed by `param_type`.
