import os
import time
import pandas as pd
import traceback

from data_updater.trading_utils import get_clob_client
from data_updater.find_markets import (
    get_all_markets,
    get_all_results,
    get_markets,
    add_volatility_to_df,
)
from data_updater.sports import build_sport_index
from data_updater.single_market import load_single_market_restriction
from poly_utils import db


def _filter_by_condition_ids(all_df, id_set):
    """Keep only rows whose (lower-cased) condition_id is in id_set."""
    if len(all_df) == 0 or 'condition_id' not in all_df.columns:
        return all_df.iloc[0:0]
    mask = all_df['condition_id'].astype(str).str.lower().isin(id_set)
    return all_df[mask].reset_index(drop=True)


def fetch_and_process_data():
    client = get_clob_client()

    all_df = get_all_markets(client)
    print(f'Got all Markets: {len(all_df)}')

    restriction = load_single_market_restriction()
    if restriction is not None:
        # Single-market override: trade exactly the requested condition_id(s),
        # bypassing both the sport filter and the reward floor.
        sport_map = {}
        all_df = _filter_by_condition_ids(all_df, restriction)
        maker_reward = 0.0
        found = set(all_df['condition_id'].astype(str).str.lower()) if len(all_df) else set()
        missing = restriction - found
        if missing:
            print(f'Single-market: {sorted(missing)} not in the reward feed (skipped).')
        print(f'Single-market mode: trading {len(all_df)} market(s).')
    else:
        # Sports-only selection: keep reward-feed markets that are also today/live
        # sport games (soccer, singles tennis, NHL, NBA, MLB, CS2+LoL esports).
        sport_map = build_sport_index()
        all_df = _filter_by_condition_ids(all_df, set(sport_map))
        maker_reward = float(os.getenv('MAKER_REWARD', '0.75'))
        print(f'Sport filter: kept {len(all_df)} reward-feed markets across enabled sports.')

    if len(all_df) == 0:
        if restriction is not None:
            # Explicit request for specific markets that aren't tradeable here: fail loudly
            # rather than silently keep trading the previous set.
            raise RuntimeError(
                f'Single-market mode: none of {sorted(restriction)} are in the CLOB reward feed; '
                'nothing to trade (non-reward markets are not supported yet).'
            )
        print(f'{pd.to_datetime("now")}: No markets after filtering; leaving table unchanged.')
        return

    all_results = get_all_results(all_df, client)
    print("Got all Results")
    _all_data, all_markets = get_markets(all_results, maker_reward=maker_reward)
    print("Got all orderbook")

    print(f'{pd.to_datetime("now")}: Fetched all markets data of length {len(all_markets)}.')
    new_df = add_volatility_to_df(all_markets)
    new_df['volatility_sum'] = new_df['24_hour'] + new_df['7_day'] + new_df['14_day']

    new_df = new_df.sort_values('volatility_sum', ascending=True)
    new_df['volatilty/reward'] = ((new_df['gm_reward_per_100'] / new_df['volatility_sum']).round(2)).astype(str)

    # Tag each market with its sport (empty in single-market mode). trading.py ignores it.
    new_df['sport'] = new_df['condition_id'].astype(str).str.lower().map(sport_map).fillna('')

    new_df = new_df[['question', 'answer1', 'answer2', 'spread', 'rewards_daily_rate', 'gm_reward_per_100', 'sm_reward_per_100', 'bid_reward_per_100', 'ask_reward_per_100',  'volatility_sum', 'volatilty/reward', 'min_size', '1_hour', '3_hour', '6_hour', '12_hour', '24_hour', '7_day', '30_day',
                     'best_bid', 'best_ask', 'volatility_price', 'max_spread', 'tick_size',
                     'neg_risk',  'market_slug', 'token1', 'token2', 'condition_id', 'sport']]

    new_df = new_df.sort_values('gm_reward_per_100', ascending=False)

    print(f'{pd.to_datetime("now")}: Prepared {len(new_df)} markets.')

    # Anti-wipe: never overwrite a populated table with a near-empty result (e.g. a
    # transient fetch failure). For legitimately small sport slates, lower the floor.
    min_markets = int(os.getenv('MIN_MARKETS_TO_WRITE', '1'))
    if len(new_df) >= min_markets:
        db.write_markets(new_df)
        print(f'{pd.to_datetime("now")}: Wrote {len(new_df)} markets to Postgres.')
    else:
        print(f'{pd.to_datetime("now")}: Not writing — only {len(new_df)} markets (MIN_MARKETS_TO_WRITE={min_markets}).')


if __name__ == "__main__":
    db.init_db()
    interval = int(os.getenv('UPDATE_INTERVAL_SECONDS', '900'))
    while True:
        try:
            fetch_and_process_data()
        except Exception as e:
            traceback.print_exc()
            print(str(e))
        time.sleep(interval)
