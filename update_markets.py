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
from poly_utils import db


def fetch_and_process_data():
    client = get_clob_client()

    all_df = get_all_markets(client)
    print("Got all Markets")
    all_results = get_all_results(all_df, client)
    print("Got all Results")
    # No human allow-list anymore: get_markets keeps every reward-bearing market.
    _all_data, all_markets = get_markets(all_results, maker_reward=0.75)
    print("Got all orderbook")

    print(f'{pd.to_datetime("now")}: Fetched all markets data of length {len(all_markets)}.')
    new_df = add_volatility_to_df(all_markets)
    new_df['volatility_sum'] = new_df['24_hour'] + new_df['7_day'] + new_df['14_day']

    new_df = new_df.sort_values('volatility_sum', ascending=True)
    new_df['volatilty/reward'] = ((new_df['gm_reward_per_100'] / new_df['volatility_sum']).round(2)).astype(str)

    new_df = new_df[['question', 'answer1', 'answer2', 'spread', 'rewards_daily_rate', 'gm_reward_per_100', 'sm_reward_per_100', 'bid_reward_per_100', 'ask_reward_per_100',  'volatility_sum', 'volatilty/reward', 'min_size', '1_hour', '3_hour', '6_hour', '12_hour', '24_hour', '7_day', '30_day',
                     'best_bid', 'best_ask', 'volatility_price', 'max_spread', 'tick_size',
                     'neg_risk',  'market_slug', 'token1', 'token2', 'condition_id']]

    new_df = new_df.sort_values('gm_reward_per_100', ascending=False)

    print(f'{pd.to_datetime("now")}: Prepared {len(new_df)} markets.')

    # Guard against a partial fetch wiping the table (same intent as the old sheet guard).
    if len(new_df) > 50:
        db.write_markets(new_df)
        print(f'{pd.to_datetime("now")}: Wrote {len(new_df)} markets to Postgres.')
    else:
        print(f'{pd.to_datetime("now")}: Not writing markets because of length {len(new_df)}.')


if __name__ == "__main__":
    db.init_db()
    while True:
        try:
            fetch_and_process_data()
            time.sleep(60 * 60)  # Sleep for an hour
        except Exception as e:
            traceback.print_exc()
            print(str(e))
