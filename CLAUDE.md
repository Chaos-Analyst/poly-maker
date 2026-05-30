# CLAUDE.md — working guide for this repo

## How to work here (read this first)

1. **Research before implementing. Never guess an SDK/API surface.**
   Verify every method name, argument, and return shape against the *actual source* before
   writing code that uses it. For the Polymarket client that means the real package
   (`py_clob_client_v2`, repo `Polymarket/py-clob-client-v2`) and Polymarket's docs — not memory,
   not a similar-looking older version. If you can't confirm it, go find it.
2. **Confirm with the user before implementing non-trivial changes.** Propose the approach, surface
   the trade-offs, and get a decision. Work step-by-step; don't expand scope on your own.
3. **Behavior-preserving by default.** When migrating/refactoring, keep public interfaces and return
   shapes identical unless the user agreed otherwise. The trading logic in `trading.py` / `poly_data/*`
   depends on the exact shapes the `PolymarketClient` wrapper returns.
4. **This bot trades real money.** Be conservative. Read-only verification first; never place/cancel
   live orders or send on-chain transactions without an explicit, in-the-moment go-ahead.

## Repo norms
- Dependencies are managed with **`uv`** (`uv sync`, `uv lock`, `uv run python ...`). Python 3.9.
- Secrets live in `.env` (`PK`, `BROWSER_ADDRESS`, `SPREADSHEET_URL`, optional `SIGNATURE_TYPE`;
  `BUILDER_API_KEY`/`BUILDER_SECRET`/`BUILDER_PASSPHRASE` for relayer merges).
- Config/markets come from a Google Sheet (see README).

## Project shape
- `poly_data/polymarket_client.py` — `PolymarketClient`, the wrapper around the Polymarket CLOB client
  (orders, order book, cancels) + web3 (USDC/CTF balances) + relayer-based position merging.
- `poly_data/` — websockets, data processing, global state, the market-making data layer.
- `trading.py` — the market-making strategy (consumes `global_state.client`, the wrapper).
- `data_updater/` — separate market-discovery tooling (`update_markets.py`, `find_markets.py`).
- `poly_stats/` — peripheral reporting to the Google Sheet.

## CLOB client: we are on `py-clob-client-v2` (v1.0.1)
This project was migrated from the legacy `py-clob-client==0.28.0` (CLOB v1) to
**`py-clob-client-v2`** (CLOB v2 / CTF Exchange V2). v2 is a rewrite — do not assume v1 APIs.

**Decisions in force:** `signature_type=1` (POLY_PROXY) with `funder=BROWSER_ADDRESS`, overridable via
the `SIGNATURE_TYPE` env var (default `1`). The wrapper's public interface is unchanged.

**v2 quick-reference (verified against source — re-verify if in doubt):**
- Imports: `from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions, OrderType,
  OpenOrderParams, OrderMarketCancelParams, BalanceAllowanceParams, AssetType, ApiCreds, Side`.
  `POLYGON`/`INITIAL_CURSOR`/`END_CURSOR` → `py_clob_client_v2.constants`; `BUY`/`SELL` →
  `py_clob_client_v2.order_builder.constants`; `create_level_2_headers`/`RequestArgs` →
  `py_clob_client_v2.headers.headers` / `py_clob_client_v2.clob_types`.
- Constructor: `ClobClient(host, chain_id, key=None, creds=None, signature_type=None, funder=None, …)`.
  Signature types: `0`=EOA, `1`=POLY_PROXY, `2`=POLY_GNOSIS_SAFE, `3`=POLY_1271 (deposit wallet).
- Auth: `create_or_derive_api_key()` (NOT `create_or_derive_api_creds`); `set_api_creds(creds)` unchanged.
  `client.creds` is `ApiCreds(api_key, api_secret, api_passphrase)`; `client.signer` is a `Signer`.
- Orders: `create_order(order_args, options=PartialCreateOrderOptions(neg_risk=…))` → signed order
  (resolves tick size / exchange version over the network, cached). `OrderArgs.side` accepts
  `"BUY"/"SELL"`. `post_order(order, OrderType.GTC)` posts. Or `create_and_post_order(...)` in one call.
- Reads: `get_open_orders(OpenOrderParams(...))` (was `get_orders`), returns `list[dict]`.
  `get_order_book(token_id)` returns a **`dict`** `{market, asset_id, bids, asks, …}` where `bids`/`asks`
  are `list[{"price","size"}]` strings — use `ob['bids']`, `ob['bids'][-1]['price']` (no `.bids`/`.price`).
  `get_balance_allowance(BalanceAllowanceParams(asset_type, token_id))` → `{'balance', …}`.
- Cancels: `cancel_market_orders(OrderMarketCancelParams(market=…/asset_id=…))` (was kwargs).
- HTTP layer is `httpx` (was `requests`).

**Websocket** is NOT part of the SDK. `poly_data/websocket_handlers.py` talks directly to
`wss://ws-subscriptions-clob.polymarket.com/ws/{market,user}`. Verified against Polymarket's current
docs that the live `book` / `price_change` (`price_changes[]`) / user `trade`/`order` schemas match what
`data_processing.py` already parses — so it was left unchanged in the migration.

## Merges: Polymarket Relayer API (pUSD)
`PolymarketClient.merge_positions` submits a CTF `mergePositions` call through the **official
`py-builder-relayer-client`** (gas-free PROXY/SAFE meta-tx; relay type tracks `SIGNATURE_TYPE`: 1→PROXY,
2→SAFE) — no Node.js, no EOA tx. It targets the **CLOB v2 collateral adapters**: `CtfCollateralAdapter`
`0xAdA100Db00Ca00073811820692005400218FcE1f` (standard) / `NegRiskCtfCollateralAdapter`
`0xadA2005600Dec949baf300f4C6120000bDB6eAab` (neg-risk), passing **USDC.e** as the `collateralToken` arg
(that's how the underlying CTF identifies the positions to burn) so the adapter returns the proceeds as
**pUSD**. Calldata is the same 5-arg `mergePositions(address,bytes32,bytes32,uint256[],uint256)` for both;
only the `to` adapter differs. Needs Builder API creds in `.env`. The old Node.js `poly_merger/` subproject
was removed.

## Known follow-ups (deferred, not done yet)
- **On-chain V2 approvals must be run once.** `data_updater/trading_utils.py:approveContracts()` was
  updated to include the CTF Exchange V2 contracts (`0xE111180000d2663C0091e4f400237545B87B996B`,
  neg-risk `0xe2222d279d744050d28e00520010520000310F59`) — but executing it is a manual step, and for a
  POLY_PROXY (sig type 1) funder, approvals normally come from the proxy (Polymarket "enable trading" UI),
  not the bare EOA. Orders won't settle on Exchange V2 until approvals exist. Likewise, relayer **merges**
  require the proxy to have approved the v2 collateral adapters as ERC-1155 operators on the CTF
  (`0x4D97…`) — normally set by the same "enable trading" UI step.
- v2 caches a token's tick size after first use; a long-running bot can hit a stale tick size if a market's
  tick changes (near 0.04/0.96), causing rejects. Mitigation (deferred): pass `tick_size` from the sheet.
- WS hardening (app-level `PING` every 10s; handle `tick_size_change`) and rewriting
  `poly_stats/account_stats.get_earnings` to v2's native rewards API are deferred.
