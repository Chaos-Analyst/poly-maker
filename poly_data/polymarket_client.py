from dotenv import load_dotenv          # Environment variable management
import os                           # Operating system interface

# Polymarket API client libraries
from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions, OrderMarketCancelParams, OrderType, BalanceAllowanceParams, AssetType, ApiCreds
from py_clob_client_v2.constants import POLYGON

# Polymarket Relayer API client (gas-free PROXY/SAFE meta-transactions; used for merges)
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.models import RelayerTxType, Transaction
from py_builder_signing_sdk.config import BuilderConfig
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

# Web3 libraries for blockchain interaction
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account

import requests                     # HTTP requests
import pandas as pd                 # Data analysis
import json                         # JSON processing

from py_clob_client_v2 import OpenOrderParams

# Smart contract ABIs
from poly_data.abis import NegRiskAdapterABI, ConditionalTokenABI, erc20_abi

# Load environment variables
load_dotenv()


class PolymarketClient:
    """
    Client for interacting with Polymarket's API and smart contracts.
    
    This class provides methods for:
    - Creating and managing orders
    - Querying order book data
    - Checking balances and positions
    - Merging positions
    
    The client connects to both the Polymarket API and the Polygon blockchain.
    """
    
    def __init__(self, pk='default') -> None:
        """
        Initialize the Polymarket client with API and blockchain connections.
        
        Args:
            pk (str, optional): Private key identifier, defaults to 'default'
        """
        host="https://clob.polymarket.com"

        # Get credentials from environment variables
        key=os.getenv("PK")
        browser_address = os.getenv("BROWSER_ADDRESS")

        # Don't print sensitive wallet information
        print("Initializing Polymarket client...")
        chain_id=POLYGON

        # signature_type: 0=EOA, 1=POLY_PROXY (default), 2=POLY_GNOSIS_SAFE, 3=POLY_1271 (deposit wallet)
        signature_type = int(os.getenv("SIGNATURE_TYPE", "1"))

        # ---- Deposit-wallet (POLY_1271) funder check ----
        # For signature_type=3 the funder MUST be the deposit wallet that THIS key owns. That wallet
        # is deterministic from the signer key, so derive it and verify BROWSER_ADDRESS matches.
        # Catches the common "the key and the funded wallet are different accounts" mistake up front
        # with a clear message, instead of failing later with cryptic order/auth rejections.
        if signature_type == 3:
            from eth_account import Account as _Account
            from py_builder_relayer_client.config import get_contract_config
            from py_builder_relayer_client.builder.derive import derive_uups_deposit_wallet
            _cfg = get_contract_config(chain_id)
            _eoa = _Account.from_key(key).address
            _derived = Web3.to_checksum_address(derive_uups_deposit_wallet(
                _eoa, _cfg.deposit_wallet_factory, _cfg.deposit_wallet_implementation))
            if not browser_address:
                browser_address = _derived
                print(f"BROWSER_ADDRESS not set; using deposit wallet derived from PK: {_derived}")
            elif Web3.to_checksum_address(browser_address) != _derived:
                raise ValueError(
                    "Deposit-wallet mismatch (SIGNATURE_TYPE=3): the key in PK does not control "
                    "BROWSER_ADDRESS.\n"
                    f"  PK signer EOA       : {_eoa}\n"
                    f"  PK's deposit wallet : {_derived}\n"
                    f"  BROWSER_ADDRESS     : {Web3.to_checksum_address(browser_address)}\n"
                    f"Fix: either put the owner key of {Web3.to_checksum_address(browser_address)} in PK, "
                    f"or set BROWSER_ADDRESS={_derived} and fund THAT wallet."
                )
            else:
                print(f"Deposit wallet verified: PK controls {_derived}")

        self.browser_wallet = Web3.to_checksum_address(browser_address)

        # Initialize the Polymarket API client (CLOB v2)
        self.client = ClobClient(
            host=host,
            chain_id=chain_id,
            key=key,
            funder=self.browser_wallet,
            signature_type=signature_type
        )

        # Set up API credentials.
        # Deposit wallets (POLY_1271 / signature_type=3) need the CLOB API key bound to the
        # DEPOSIT WALLET, but create_or_derive_api_key() binds it to the EOA signer
        # (py-clob-client-v2 L1-auth limitation), which makes orders fail with
        # "the order signer address has to be the address of the API KEY". So, matching the
        # docs' deposit-wallet example, accept ready-made creds from the environment when present.
        clob_key = os.getenv("CLOB_API_KEY")
        clob_secret = os.getenv("CLOB_SECRET")
        clob_pass = os.getenv("CLOB_PASS_PHRASE") or os.getenv("CLOB_PASSPHRASE")
        if clob_key and clob_secret and clob_pass:
            print("Using CLOB API creds from env (CLOB_API_KEY/CLOB_SECRET/CLOB_PASS_PHRASE)")
            self.creds = ApiCreds(api_key=clob_key, api_secret=clob_secret, api_passphrase=clob_pass)
        else:
            self.creds = self.client.create_or_derive_api_key()
        self.client.set_api_creds(creds=self.creds)
        
        # Initialize Web3 connection to Polygon. polygon-rpc.com now rate-limits/401s, so default to
        # a reliable public node and allow override via RPC_URL (use your own Alchemy/Infura/QuickNode
        # for production reliability).
        rpc_url = os.getenv("RPC_URL") or "https://polygon-bor-rpc.publicnode.com"
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        
        # Set up USDC contract for balance checks
        self.usdc_contract = web3.eth.contract(
            address="0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174", 
            abi=erc20_abi
        )

        # Store key contract addresses
        self.addresses = {
            'neg_risk_adapter': '0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296',
            'collateral': '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174',
            'conditional_tokens': '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045',
            # CLOB v2 collateral adapters: merge/redeem through these so proceeds
            # are returned as pUSD (the v2 collateral) instead of USDC.e.
            'ctf_collateral_adapter': '0xAdA100Db00Ca00073811820692005400218FcE1f',
            'neg_risk_ctf_collateral_adapter': '0xadA2005600Dec949baf300f4C6120000bDB6eAab',
        }

        # Initialize contract interfaces
        self.neg_risk_adapter = web3.eth.contract(
            address=self.addresses['neg_risk_adapter'], 
            abi=NegRiskAdapterABI
        )

        self.conditional_tokens = web3.eth.contract(
            address=self.addresses['conditional_tokens'], 
            abi=ConditionalTokenABI
        )

        self.web3 = web3

        # ----- Polymarket Relayer API client (used by merge_positions) -----
        # Submit merges as gas-free meta-transactions through the Polymarket Relayer.
        # The relay type tracks the CLOB signature_type so merges use the same wallet
        # model as trading: 1 (POLY_PROXY) -> PROXY, 2 (POLY_GNOSIS_SAFE) -> SAFE.
        self.signature_type = signature_type
        self.relay_tx_type = RelayerTxType.PROXY if signature_type == 1 else RelayerTxType.SAFE

        builder_config = self._build_builder_config()
        if builder_config is None:
            print(
                "WARNING: Builder API credentials not set "
                "(BUILDER_API_KEY/BUILDER_SECRET/BUILDER_PASSPHRASE); "
                "merge_positions() will fail until they are added to .env."
            )

        self.relay_client = RelayClient(
            "https://relayer-v2.polymarket.com",
            chain_id,
            private_key=key,
            builder_config=builder_config,
            relay_tx_type=self.relay_tx_type,
            rpc_url=rpc_url,
        )

    def _build_builder_config(self):
        """Load Builder API credentials from the environment into a BuilderConfig.

        Returns None when any credential is missing so the client can still start;
        merge_positions() raises a clear error if it is called without credentials.
        """
        key = os.getenv("BUILDER_API_KEY") or os.getenv("BUILDER_KEY")
        secret = os.getenv("BUILDER_SECRET")
        passphrase = os.getenv("BUILDER_PASSPHRASE") or os.getenv("BUILDER_PASS_PHRASE")
        if not (key and secret and passphrase):
            return None
        return BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=key, secret=secret, passphrase=passphrase
            )
        )

    def create_order(self, marketId, action, price, size, neg_risk=False, post_only=False):
        """
        Create and submit a new order to the Polymarket order book.
        
        Args:
            marketId (str): ID of the market token to trade
            action (str): "BUY" or "SELL"
            price (float): Order price (0-1 range for prediction markets)
            size (float): Order size in USDC
            neg_risk (bool, optional): Whether this is a negative risk market. Defaults to False.
            
        Returns:
            dict: Response from the API containing order details, or empty dict on error
        """
        # Create order parameters
        order_args = OrderArgs(
            token_id=str(marketId),
            price=price,
            size=size,
            side=action
        )

        # Build and sign the order. neg_risk is passed explicitly (for both True/False) so v2
        # doesn't have to make an extra network call to resolve it. v2 still resolves the tick
        # size and exchange version over the network on first use and caches them.
        signed_order = self.client.create_order(
            order_args, options=PartialCreateOrderOptions(neg_risk=neg_risk)
        )

        try:
            # Submit the signed order to the API as a resting limit order (GTC). post_only=True
            # makes it maker-only: if it would cross and execute as a taker, the API rejects it
            # instead of filling -- keeping fills reward-eligible. (GTC supports post_only; FOK/FAK
            # do not.)
            resp = self.client.post_order(signed_order, OrderType.GTC, post_only=post_only)
            return resp
        except Exception as ex:
            # Surface the failure (instead of swallowing to {}) so callers can detect an
            # insufficient balance/allowance rejection and react (e.g. merge to free collateral).
            if post_only:
                # A post-only order that would cross is rejected by design -- a clean skip, not a bug.
                print(f"Order not placed (post-only would cross/take, expected): {ex}")
            else:
                print(ex)
            return {"success": False, "errorMsg": str(ex)}

    @staticmethod
    def is_balance_error(resp):
        """True if an order response indicates an insufficient balance/allowance rejection.

        The CLOB returns this either as a non-200 (surfaced here as an errorMsg string via
        create_order's except block) or as a 200 body with success=false + errorMsg. We match
        on the words 'balance'/'allowance' rather than an exact string to be wording-robust.
        """
        if not isinstance(resp, dict):
            return False
        msg = str(resp.get("errorMsg") or resp.get("error") or "").lower()
        return "balance" in msg or "allowance" in msg

    def get_collateral_balance(self):
        """Free (spendable) CLOB collateral in whole units, or None if unavailable.

        This is the authoritative "can I place more orders" balance (reflects funds tied up in
        open orders/positions), used to decide when to merge mergeable pairs to recycle capital.
        """
        try:
            resp = self.client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
            bal = resp.get("balance") if isinstance(resp, dict) else None
            return float(bal) / 1e6 if bal is not None else None
        except Exception as ex:
            print(f"get_collateral_balance failed: {ex}")
            return None

    def get_order_book(self, market):
        """
        Get the current order book for a specific market.
        
        Args:
            market (str): Market ID to query
            
        Returns:
            tuple: (bids_df, asks_df) - DataFrames containing bid and ask orders
        """
        orderBook = self.client.get_order_book(market)
        # v2 returns a dict with 'bids'/'asks' as lists of {"price","size"} (strings)
        return pd.DataFrame(orderBook['bids']).astype(float), pd.DataFrame(orderBook['asks']).astype(float)


    def get_usdc_balance(self):
        """
        Get the USDC balance of the connected wallet.
        
        Returns:
            float: USDC balance in decimal format
        """
        return self.usdc_contract.functions.balanceOf(self.browser_wallet).call() / 10**6
     
    def get_pos_balance(self):
        """
        Get the total value of all positions for the connected wallet.
        
        Returns:
            float: Total position value in USDC
        """
        res = requests.get(f'https://data-api.polymarket.com/value?user={self.browser_wallet}')
        return float(res.json()['value'])

    def get_total_balance(self):
        """
        Get the combined value of USDC balance and all positions.
        
        Returns:
            float: Total account value in USDC
        """
        return self.get_usdc_balance() + self.get_pos_balance()

    def get_all_positions(self):
        """
        Get all positions for the connected wallet across all markets.
        
        Returns:
            DataFrame: All positions with details like market, size, avgPrice
        """
        res = requests.get(f'https://data-api.polymarket.com/positions?user={self.browser_wallet}')
        return pd.DataFrame(res.json())
    
    def get_raw_position(self, tokenId):
        """
        Get the raw token balance for a specific market outcome token.
        
        Args:
            tokenId (int): Token ID to query
            
        Returns:
            int: Raw token amount (before decimal conversion)
        """
        return int(self.conditional_tokens.functions.balanceOf(self.browser_wallet, int(tokenId)).call())

    def get_position(self, tokenId):
        """
        Get both raw and formatted position size for a token.
        
        Args:
            tokenId (int): Token ID to query
            
        Returns:
            tuple: (raw_position, shares) - Raw token amount and decimal shares
                   Shares less than 1 are treated as 0 to avoid dust amounts
        """
        raw_position = self.get_raw_position(tokenId)
        shares = float(raw_position / 1e6)

        # Ignore very small positions (dust)
        if shares < 1:
            shares = 0

        return raw_position, shares
    
    def get_all_orders(self):
        """
        Get all open orders for the connected wallet.
        
        Returns:
            DataFrame: All open orders with their details
        """
        orders_df = pd.DataFrame(self.client.get_open_orders())

        # Convert numeric columns to float
        for col in ['original_size', 'size_matched', 'price']:
            if col in orders_df.columns:
                orders_df[col] = orders_df[col].astype(float)

        return orders_df

    def get_market_orders(self, market):
        """
        Get all open orders for a specific market.
        
        Args:
            market (str): Market ID to query
            
        Returns:
            DataFrame: Open orders for the specified market
        """
        orders_df = pd.DataFrame(self.client.get_open_orders(OpenOrderParams(
            market=market,
        )))

        # Convert numeric columns to float
        for col in ['original_size', 'size_matched', 'price']:
            if col in orders_df.columns:
                orders_df[col] = orders_df[col].astype(float)

        return orders_df
    

    def cancel_all_asset(self, asset_id):
        """
        Cancel all orders for a specific asset token.
        
        Args:
            asset_id (str): Asset token ID
        """
        self.client.cancel_market_orders(OrderMarketCancelParams(asset_id=str(asset_id)))


    
    def cancel_all_market(self, marketId):
        """
        Cancel all orders in a specific market.
        
        Args:
            marketId (str): Market ID
        """
        self.client.cancel_market_orders(OrderMarketCancelParams(market=marketId))

    
    def merge_positions(self, amount_to_merge, condition_id, is_neg_risk_market):
        """
        Merge complementary YES+NO positions back into collateral via the
        Polymarket Relayer API.

        Routes a CTF ``mergePositions`` call through the CLOB v2 collateral adapter,
        which performs the merge on the underlying ConditionalTokens contract and
        returns the proceeds as pUSD (the v2 collateral). The relayer submits it as a
        gas-free PROXY/SAFE meta-transaction, so no on-chain tx is sent from the EOA.

        Args:
            amount_to_merge (int): Raw token amount to merge (6-decimal base units).
            condition_id (str): Market condition id (0x-prefixed 32-byte hex).
            is_neg_risk_market (bool): Whether this is a negative-risk market.

        Returns:
            str: The on-chain transaction hash.

        Raises:
            Exception: If builder credentials are missing or the merge fails/times out.
        """
        if self.relay_client.builder_config is None:
            raise Exception(
                "Cannot merge: Builder API credentials missing. Set BUILDER_API_KEY, "
                "BUILDER_SECRET and BUILDER_PASSPHRASE in .env."
            )

        # Route through the v2 collateral adapter so proceeds come back as pUSD. Both
        # adapters expose the same 5-arg mergePositions selector, so the calldata is
        # identical -- only the target address differs.
        adapter = Web3.to_checksum_address(
            self.addresses['neg_risk_ctf_collateral_adapter']
            if is_neg_risk_market
            else self.addresses['ctf_collateral_adapter']
        )

        # mergePositions(collateralToken, parentCollectionId, conditionId, partition, amount)
        # collateralToken stays USDC.e: that is how the underlying CTF identifies the
        # positions to burn; the adapter wraps the returned USDC.e into pUSD.
        calldata = self.conditional_tokens.encode_abi(
            abi_element_identifier="mergePositions",
            args=[
                Web3.to_checksum_address(self.addresses['collateral']),
                bytes(32),                            # parentCollectionId (ZERO_BYTES32)
                Web3.to_bytes(hexstr=condition_id),   # conditionId (bytes32)
                [1, 2],                               # partition (YES / NO)
                int(amount_to_merge),
            ],
        )

        print(
            f"Merging {amount_to_merge} via relayer "
            f"({self.relay_tx_type.value}, neg_risk={is_neg_risk_market}) "
            f"through adapter {adapter}"
        )

        if self.signature_type == 3:
            # Deposit wallet (POLY_1271): merges go through the relayer's WALLET batch flow, not
            # PROXY/SAFE. The deposit wallet (funder) calls the adapter; the owner key signs the
            # batch. NOTE: requires the deposit wallet to have approved the adapter as an ERC-1155
            # operator on the CTF (normally done by Polymarket's "enable trading"); if the first
            # merge reverts with an operator/allowance error, that approval still needs to be set.
            import time as _time
            from py_builder_relayer_client.models import DepositWalletCall, TransactionType
            nonce_payload = self.relay_client.get_nonce(
                self.relay_client.signer.address(), TransactionType.WALLET.value
            )
            wallet_nonce = str(nonce_payload["nonce"])
            resp = self.relay_client.execute_deposit_wallet_batch(
                calls=[DepositWalletCall(target=adapter, value="0", data=calldata)],
                wallet_address=self.browser_wallet,
                nonce=wallet_nonce,
                deadline=str(int(_time.time()) + 600),
            )
        else:
            resp = self.relay_client.execute(
                [Transaction(to=adapter, data=calldata, value="0")],
                "merge positions",
            )
        result = resp.wait()  # txn dict on mined/confirmed, None on failure/timeout

        if not result:
            raise Exception(
                f"Error in merging positions: relayer transaction "
                f"{resp.transaction_id} failed or timed out"
            )

        tx_hash = result.get("transactionHash") or resp.transaction_hash
        print(f"Done merging. tx={tx_hash}")
        return tx_hash