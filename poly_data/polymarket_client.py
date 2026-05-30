from dotenv import load_dotenv          # Environment variable management
import os                           # Operating system interface

# Polymarket API client libraries
from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions, OrderMarketCancelParams, OrderType
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
        self.browser_wallet=Web3.to_checksum_address(browser_address)

        # Initialize the Polymarket API client (CLOB v2)
        # signature_type: 1=POLY_PROXY (default), 2=POLY_GNOSIS_SAFE, 3=POLY_1271 (deposit wallet)
        signature_type = int(os.getenv("SIGNATURE_TYPE", "1"))
        self.client = ClobClient(
            host=host,
            chain_id=chain_id,
            key=key,
            funder=self.browser_wallet,
            signature_type=signature_type
        )

        # Set up API credentials
        self.creds = self.client.create_or_derive_api_key()
        self.client.set_api_creds(creds=self.creds)
        
        # Initialize Web3 connection to Polygon
        web3 = Web3(Web3.HTTPProvider("https://polygon-rpc.com"))
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
            rpc_url="https://polygon-rpc.com",
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

    def create_order(self, marketId, action, price, size, neg_risk=False):
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
            # Submit the signed order to the API as a resting limit order (GTC)
            resp = self.client.post_order(signed_order, OrderType.GTC)
            return resp
        except Exception as ex:
            print(ex)
            return {}

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