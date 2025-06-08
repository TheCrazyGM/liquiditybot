# Liquidity Bot for Hive Engine

A Python-based bot for managing liquidity and performing automated trades for token pairs on the Hive Engine platform. It utilizes `hive-nectar` and `nectarengine` libraries.

## Features

- Automated trading for specified token pairs on Hive Engine (e.g., SWAP.HIVE:NECTAR).
- Operates based on configurable price thresholds, target amounts, and optional price range bounds.
- Fetches real-time pool data from Hive Engine.
- Handles Hive Engine transaction submission and confirmation.
- Configuration via a `config.json` file for credentials and some parameters.
- Supports command-line arguments for dynamic control over trading parameters, including dry runs.
- Logging for monitoring bot operations.

## Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended for environment and package management) or `pip`.
- Git

## Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/thecrazygm/liquiditybot.git
   cd liquiditybot
   ```

2. **Set up the environment and install dependencies:**

   **Using `uv` (recommended):**

   ```bash
   uv venv  # Create virtual environment in .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   # The script's shebang `uv run --script` can also handle dependencies if run directly.
   # For development and to use pyproject.toml defined dependencies:
   uv pip install -e .[dev]
   ```

   **Using `pip` and `venv`:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .[dev]
   ```

   This installs the bot and its dependencies (`hive-nectar`, `nectarengine`) along with development tools.

## Configuration

The bot requires a `config.json` file in the same directory as `liquidityBot.py` (or specified via `--config` argument).

1. **Create `config.json`:**

   ```json
   {
     "hive": {
       "accountName": "YOUR_HIVE_ACCOUNT_NAME",
       "activeKey": "YOUR_HIVE_ACTIVE_KEY"
     }
     // Add other configurations as needed by the bot
   }
   ```

   **Important:** `activeKey` is your Hive Active Key and is required for trading operations. Secure this file appropriately.

2. **Environment Variables (Optional):**

   Some parameters can be set via environment variables:

   - `HIVE_ENGINE_TX_CONFIRM_DELAY_SECONDS`
   - `MAX_TX_INFO_RETRIES`
   - `TX_INFO_RETRY_DELAY_SECONDS`

## Usage

The bot is run from the command line. Here are the primary ways to execute it:

**1. Directly as a script (recommended for `uv` users):**

This method leverages the script's shebang (`#!/usr/bin/env -S uv run --quiet --script`) which allows `uv` to manage dependencies automatically.

First, ensure the script is executable:

```bash
chmod +x liquidityBot.py
```

Then, run it from the repository root:

```bash
./liquidityBot.py --target-asset PIZZA --base-currency SWAP.HIVE --amount 100 --threshold 0.05
```

**2. Using the Python interpreter:**

This method is suitable if you've manually set up a virtual environment and installed dependencies.

First, activate your virtual environment (e.g., `source .venv/bin/activate`). Then run:

```bash
python liquidityBot.py --target-asset PIZZA --base-currency SWAP.HIVE --amount 100 --threshold 0.05 --dry-run
```

### Command-Line Arguments

The script accepts several arguments:

- `-t`, `--target-asset <ASSET_SYMBOL>`: Symbol of the target asset to trade (default: PIZZA).
- `-b`, `--base-currency <CURRENCY_SYMBOL>`: Symbol of the base currency for trading (default: SWAP.HIVE).
- `-a`, `--amount <NUMBER>`: Amount of the target asset to trade (default: 50).
- `-p`, `--threshold <PRICE>`: Price threshold in base currency to trigger sell (default: 0.047).
- `-acc`, `--account <HIVE_ACCOUNT_NAME>`: Override Hive account name from `config.json` (default: loaded from `config.json`).
- `-d`, `--dry-run`: Simulate transactions without broadcasting.

Use `python liquidityBot.py --help` for the most up-to-date list and details.

## Development

This project uses `ruff` for linting/formatting.

### Linting and Formatting

```bash
ruff check .
ruff format .
```

## Contributing

Contributions are welcome! If you'd like to contribute, please:

1. Fork the repository.
2. Create a new branch for your feature or bug fix.
3. Make your changes and commit them with clear messages.
4. Ensure code is linted.
5. Open a Pull Request.

Please open an issue first to discuss any significant changes.

## License

This project is licensed under the MIT License.
