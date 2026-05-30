# Poly-Maker

A market making bot for Polymarket prediction markets. This bot automates the process of providing liquidity to markets on Polymarket by maintaining orders on both sides of the book with configurable parameters. A summary of my experience running this bot is available [here](https://x.com/defiance_cr/status/1906774862254800934)

## Overview

Poly-Maker is a comprehensive solution for automated market making on Polymarket. It includes:

- Real-time order book monitoring via WebSockets
- Position management with risk controls
- Customizable trade parameters fetched from Google Sheets
- Automated position merging functionality
- Sophisticated spread and price management

## Structure

The repository consists of several interconnected modules:

- `poly_data`: Core data management and market making logic (including relayer-based position merging)
- `poly_stats`: Account statistics tracking
- `poly_utils`: Shared utility functions
- `data_updater`: Separate module for collecting market information

## Requirements

- Python 3.9.10 or higher
- Google Sheets API credentials
- Polymarket account and API credentials (including Builder API credentials for position merging)

## Installation

This project uses UV for fast, reliable package management.

### Install UV

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or with pip
pip install uv
```

### Install Dependencies

```bash
# Install all dependencies
uv sync

# Install with development dependencies (black, pytest)
uv sync --extra dev
```

### Quick Start

```bash
# Run the market maker (recommended)
uv run python main.py

# Update market data
uv run python update_markets.py

# Update statistics
uv run python update_stats.py
```

### Setup Steps

#### 1. Clone the repository

```bash
git clone https://github.com/yourusername/poly-maker.git
cd poly-maker
```

#### 2. Install Python dependencies

```bash
uv sync
```

#### 3. Set up environment variables

```bash
cp .env.example .env
```

#### 4. Configure your credentials in `.env`

Edit the `.env` file with your credentials:
- `PK`: Your private key for Polymarket
- `BROWSER_ADDRESS`: Your Polymarket proxy/Safe wallet address (the order "funder")
- `SIGNATURE_TYPE`: `1` for a Polymarket proxy wallet (default), `2` for a Gnosis Safe
- `BUILDER_API_KEY` / `BUILDER_SECRET` / `BUILDER_PASSPHRASE`: Builder API credentials, required for position merging via the relayer

**Important:** Make sure your wallet has done at least one trade through the UI so that the permissions are proper (this also sets the on-chain approvals the relayer merge relies on).

#### 5. Set up Google Sheets integration

- Create a Google Service Account and download credentials to the main directory
- Copy the [sample Google Sheet](https://docs.google.com/spreadsheets/d/1Kt6yGY7CZpB75cLJJAdWo7LSp9Oz7pjqfuVWwgtn7Ns/edit?gid=1884499063#gid=1884499063)
- Add your Google service account to the sheet with edit permissions
- Update `SPREADSHEET_URL` in your `.env` file

#### 6. Update market data

Run the market data updater to fetch all available markets:

```bash
uv run python update_markets.py
```

This should run continuously in the background (preferably on a different IP than your trading bot).

- Add markets you want to trade to the "Selected Markets" sheet
- Select markets from the "Volatility Markets" sheet
- Configure parameters in the "Hyperparameters" sheet (default parameters that worked well in November are included)

#### 7. Start the market making bot

```bash
uv run python main.py
```
> [!WARNING]
> In today's market, this bot is not profitable and will lose money. Use it as a reference implementation for building your own market making strategies, not as a ready-to-deploy solution. Given the increased competition on Polymarket, I don't see a point in playing with this unless you're willing to dedicate a significant amount of time.

 
## Configuration

The bot is configured via a Google Spreadsheet with several worksheets:

- **Selected Markets**: Markets you want to trade
- **All Markets**: Database of all markets on Polymarket
- **Hyperparameters**: Configuration parameters for the trading logic


## Position Merging

When the bot holds both the YES and NO outcomes of the same market above a threshold, it merges them
back into collateral to free up capital. Merges are submitted through the **Polymarket Relayer API**
(via the official `py-builder-relayer-client`) as gas-free PROXY/SAFE meta-transactions, routed through
the CLOB v2 collateral adapters so the proceeds are returned as **pUSD**. This requires Builder API
credentials in `.env` (`BUILDER_API_KEY` / `BUILDER_SECRET` / `BUILDER_PASSPHRASE`) and replaces the
previous Node.js merger. See `PolymarketClient.merge_positions` in `poly_data/polymarket_client.py`.

## Important Notes

- This code interacts with real markets and can potentially lose real money
- Test thoroughly with small amounts before deploying with significant capital
- The `data_updater` is technically a separate repository but is included here for convenience

## License

MIT
