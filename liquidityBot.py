#!/usr/bin/env -S uv run --quiet --script
"""
Automated liquidity provision and token trading bot for the Hive Engine platform.

This script connects to Hive Engine, monitors token pair prices, and executes
trades based on predefined thresholds and amounts. It requires a config.json
file for Hive account credentials and uses environment variables for certain
transaction parameters.
"""
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "hive-nectar",
#     "nectarengine",
# ]
#
# [tool.uv.sources]
# hive-nectar = { git = "https://github.com/thecrazygm/hive-nectar" }
# nectarengine = { git = "https://github.com/thecrazygm/nectarengine" }
# ///

# Standard library imports
import argparse
import json
import logging
import os
import sys
import time
from decimal import ROUND_DOWN, Decimal

# Third-party imports
from nectar import Hive
from nectar.nodelist import NodeList

# Nectarengine imports
from nectarengine.api import Api
from nectarengine.exceptions import (
    InsufficientTokenAmount,
    PoolDoesNotExist,
    TokenNotInWallet,
    TransactionConfirmationError,
)
from nectarengine.pool import LiquidityPool
from nectarengine.poolobject import Pool
from nectarengine.tokenobject import Token
from nectarengine.wallet import Wallet

# --- Script Configuration ---
HIVE_ENGINE_TX_CONFIRM_DELAY_SECONDS = int(
    os.getenv("HIVE_ENGINE_TX_CONFIRM_DELAY_SECONDS", "10")
)  # Initial wait for HE tx to be queryable
MAX_TX_INFO_RETRIES = int(
    os.getenv("MAX_TX_INFO_RETRIES", "3")
)  # Number of retries for fetching tx info
TX_INFO_RETRY_DELAY_SECONDS = int(
    os.getenv("TX_INFO_RETRY_DELAY_SECONDS", "10")
)  # Delay between retries
CONFIG_FILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.json"
)


# Default operational parameters (can be overridden by command-line args)
DEFAULT_PRICE_THRESHOLD = Decimal("0.047")
DEFAULT_AMOUNT_TO_TRADE = Decimal("50")

# Hive Engine API and Hive nodes
HE_API_URL = "https://enginerpc.com/"
nodelist = NodeList()
HIVE_NODES = nodelist.get_hive_nodes()

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- Global Variables for Credentials (loaded from config.json) ---
HIVE_ACCOUNT_NAME = None
HIVE_ACTIVE_KEY = None


def load_credentials():
    """Load Hive account credentials from the config.json file."""
    global HIVE_ACCOUNT_NAME, HIVE_ACTIVE_KEY
    try:
        with open(CONFIG_FILE_PATH) as f:
            config_data = json.load(f)

        hive_config = config_data.get("hive")
        if not hive_config:
            logging.error(f"'hive' section not found in {CONFIG_FILE_PATH}")
            return False

        HIVE_ACCOUNT_NAME = hive_config.get("accountName")
        HIVE_ACTIVE_KEY = hive_config.get("activeKey")

        if not HIVE_ACCOUNT_NAME:
            logging.error(
                f"'accountName' not found in 'hive' section of {CONFIG_FILE_PATH}"
            )
            return False

        if not HIVE_ACTIVE_KEY:
            logging.critical(
                f"'activeKey' not found or is empty in 'hive' section of {CONFIG_FILE_PATH}."
            )
            logging.critical(
                "A valid Hive ACTIVE KEY is REQUIRED for selling tokens and liquidity pool operations."
            )
            logging.critical("Please update config.json with the correct active key.")
            return False

        return True
    except FileNotFoundError:
        logging.error(f"Configuration file {CONFIG_FILE_PATH} not found.")
        return False
    except json.JSONDecodeError:
        logging.error(f"Could not decode JSON from {CONFIG_FILE_PATH}.")
        return False
    except Exception as e:
        logging.error(f"Error loading credentials: {e}")
        return False


def fetch_current_pool_price(token_pair_str: str, he_api_client: Api) -> Decimal | None:
    """
    Fetch the current 'quotePrice' of the token pair using nectarengine.Pool.

    For a pair like 'BASE:QUOTE', returns price of QUOTE in BASE units.
    Example: For 'BASE_CURRENCY:TARGET_ASSET', returns BASE_CURRENCY per TARGET_ASSET.
    """
    try:
        # The Api object for nectarengine will be implicitly created by Pool if not passed
        # or can be passed if specific configuration is needed.
        # For this script, we'll let Pool handle its default Api instantiation.
        pool_obj = Pool(token_pair_str, api=he_api_client)
        quote_price = pool_obj.get_quote_price()

        if quote_price is not None:
            logging.info(f"Fetched pool quotePrice for {token_pair_str}: {quote_price}")
            return quote_price
        else:
            logging.warning(
                f"Could not retrieve quotePrice for {token_pair_str}. Pool data: {pool_obj}"
            )
            return None
    except Exception as e:
        # Catching a broad exception here, consider more specific ones like PoolDoesNotExist if appropriate
        logging.error(f"Error fetching pool price for {token_pair_str}: {e}")
        return None


def format_amount(amount: Decimal, precision: int) -> str:
    """Format a Decimal amount to a string with specified precision."""
    quantizer = Decimal(f"1e-{precision}")
    # Always round down to avoid attempting to spend more than available or exceeding precision
    return str(amount.quantize(quantizer, rounding=ROUND_DOWN))


def confirm_hive_engine_transaction(  # noqa: C901
    he_api_client: Api,  # Changed from hv_client: Hive
    tx_id: str,
    initial_delay_seconds: int = HIVE_ENGINE_TX_CONFIRM_DELAY_SECONDS,
    max_retries: int = MAX_TX_INFO_RETRIES,
    retry_delay_seconds: int = TX_INFO_RETRY_DELAY_SECONDS,
):
    """
    Confirm a Hive Engine transaction by polling and checking its logs.

    Uses global configuration for delays and retries.
    """
    if not he_api_client:
        msg = (
            "nectarengine.Api client instance not available for confirming transaction."
        )
        logging.error(msg)
        raise TransactionConfirmationError(msg)

    logging.info(
        f"Waiting {initial_delay_seconds}s before first check for tx {tx_id}..."
    )
    time.sleep(initial_delay_seconds)

    for attempt in range(max_retries):  # 0 to max_retries-1
        logging.info(f"Confirmation attempt {attempt + 1}/{max_retries} for tx {tx_id}")
        try:
            tx_info = he_api_client.get_transaction_info(
                txid=tx_id
            )  # Use nectarengine.Api

            if (
                tx_info
                and isinstance(tx_info, dict)
                and tx_info.get("blockNumber", 0) > 0
            ):  # Corrected key to blockNumber
                logging.info(
                    f"Transaction {tx_id} confirmed in block {tx_info['blockNumber']}."
                )  # Corrected key to blockNumber
                if "logs" in tx_info and tx_info["logs"]:
                    try:
                        logs_data = json.loads(tx_info["logs"])
                        if "errors" in logs_data and logs_data["errors"]:
                            error_message = f"Transaction {tx_id} confirmed on chain but failed with Hive Engine errors: {logs_data['errors']}"
                            logging.error(error_message)
                            raise TransactionConfirmationError(
                                error_message
                            )  # Fail immediately on HE error
                        else:
                            logging.info(
                                f"Transaction {tx_id} confirmed successfully by Hive Engine."
                            )
                            return tx_info
                    except json.JSONDecodeError as je:
                        error_message = f"Could not parse logs for transaction {tx_id}: {tx_info['logs']}. Error: {str(je)}"
                        logging.error(error_message)
                        if attempt == max_retries - 1:  # Last attempt
                            raise TransactionConfirmationError(error_message) from je
                else:  # block_num > 0 but no logs or empty logs
                    logging.warning(
                        f"Transaction {tx_id} confirmed on chain (block {tx_info['blockNumber']}), but no Hive Engine 'logs' field or logs are empty."
                    )  # Corrected key to blockNumber
                    if attempt == max_retries - 1:  # Last attempt
                        raise TransactionConfirmationError(
                            f"Transaction {tx_id} confirmed on chain but no Hive Engine logs found after {max_retries} attempts."
                        )
            else:  # Not confirmed on chain yet
                logging.info(
                    f"Transaction {tx_id} not yet confirmed on chain or not found. tx_info: {json.dumps(tx_info) if tx_info else 'None'}"
                )

        except Exception as e:
            logging.warning(
                f"Error during transaction confirmation attempt {attempt + 1} for {tx_id}: {str(e)}"
            )
            if attempt == max_retries - 1:  # Last attempt
                raise TransactionConfirmationError(
                    f"Transaction {tx_id} failed to confirm after {max_retries} retries due to error: {str(e)}"
                ) from e

        if attempt < max_retries - 1:  # If not the last attempt
            logging.info(
                f"Waiting {retry_delay_seconds}s before next attempt for tx {tx_id}..."
            )
            time.sleep(retry_delay_seconds)

    # If loop finishes, it means all retries exhausted without success
    final_msg = (
        f"Transaction {tx_id} could not be confirmed after {max_retries} attempts."
    )
    logging.error(final_msg)
    raise TransactionConfirmationError(final_msg)


def main():  # noqa: C901
    """
    Run the main function for the liquidity bot.

    Parses command-line arguments, loads configuration, initializes API clients,
    fetches token precisions, and enters the main trading loop.
    """
    parser = argparse.ArgumentParser(
        description="Automated token trading and LP management bot."
    )
    parser.add_argument(
        "-t",
        "--target-asset",
        type=str,
        default="PIZZA",
        help="Symbol of the target asset to trade (default: PIZZA).",
    )
    parser.add_argument(
        "-b",
        "--base-currency",
        type=str,
        default="SWAP.HIVE",
        help="Symbol of the base currency for trading (default: SWAP.HIVE).",
    )
    parser.add_argument(
        "-a",
        "--amount",
        type=Decimal,
        default=DEFAULT_AMOUNT_TO_TRADE,
        help=f"Amount of the target asset to trade (default: {DEFAULT_AMOUNT_TO_TRADE}).",
    )
    parser.add_argument(
        "-p",
        "--threshold",
        type=Decimal,
        default=DEFAULT_PRICE_THRESHOLD,
        help=f"Price threshold in base currency to trigger sell (default: {DEFAULT_PRICE_THRESHOLD}).",
    )
    # Account can be overridden by arg, but primarily comes from config.json
    parser.add_argument(
        "-acc",
        "--account",
        type=str,
        default=None,
        help="Override Hive account name from config.json (default: loaded from config.json)",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Simulate transactions without broadcasting.",
    )

    args = parser.parse_args()

    # Dynamically fetch token precisions
    he_api_client_for_precision = Api(url=HE_API_URL)

    try:
        target_asset_obj = Token(args.target_asset, api=he_api_client_for_precision)
        target_asset_info = target_asset_obj.get_info()
        if not target_asset_info or target_asset_info.get("precision") is None:
            logging.error(
                f"Precision not found for target asset '{args.target_asset}' after fetching info. Exiting."
            )
            sys.exit(1)
        target_asset_precision = int(target_asset_info["precision"])
    except Exception as e:
        logging.error(
            f"Could not fetch token info or precision for target asset '{args.target_asset}': {e}. Exiting."
        )
        sys.exit(1)
    logging.info(f"Fetched precision for {args.target_asset}: {target_asset_precision}")

    try:
        base_currency_obj = Token(args.base_currency, api=he_api_client_for_precision)
        base_currency_info = base_currency_obj.get_info()
        if not base_currency_info or base_currency_info.get("precision") is None:
            logging.error(
                f"Precision not found for base currency '{args.base_currency}' after fetching info. Exiting."
            )
            sys.exit(1)
        base_currency_precision = int(base_currency_info["precision"])
    except Exception as e:
        logging.error(
            f"Could not fetch token info or precision for base currency '{args.base_currency}': {e}. Exiting."
        )
        sys.exit(1)
    logging.info(
        f"Fetched precision for {args.base_currency}: {base_currency_precision}"
    )

    # Update parser description for clarity in logs if needed, though --help won't reflect this.
    parser.description = f"Automated {args.target_asset} token trading and LP management against {args.base_currency}."

    if not load_credentials():  # Load HIVE_ACCOUNT_NAME and HIVE_ACTIVE_KEY
        return 1

    # Use account from arg if provided, otherwise from config
    account_name = args.account if args.account else HIVE_ACCOUNT_NAME
    amount_to_trade = args.amount
    price_threshold = args.threshold

    if not HIVE_ACTIVE_KEY:
        # This check is somewhat redundant due to load_credentials, but good for clarity
        logging.error(
            "HIVE_ACTIVE_KEY not loaded. Check config.json."
        )  # Should be caught by load_credentials
        return 1

    if not account_name:
        logging.error(
            "HIVE_ACCOUNT_NAME not configured. Check config.json or use --account argument."
        )  # Should be caught by load_credentials
        return 1

    logging.info(f"--- {args.target_asset} Trader Bot ---")
    logging.info(f"Account: {account_name}")
    logging.info(
        f"Trading {amount_to_trade} {args.target_asset} if LP price > {price_threshold} {args.base_currency}"
    )
    if args.dry_run:
        logging.warning("DRY RUN MODE ENABLED - No transactions will be broadcast.")

    try:
        # he_api object is no longer needed directly; nectarengine objects will manage API calls.
        hv = Hive(
            node=HIVE_NODES, keys=[HIVE_ACTIVE_KEY]
        )  # Ensure HIVE_NODES is defined

        if not hv and not args.dry_run:
            # This check is primarily for ensuring HIVE_ACTIVE_KEY was loaded for broadcasting.
            # nectarengine read-only calls (like Pool price fetching) can work without keys.
            logging.error(
                f"Hive client could not be initialized (HIVE_ACTIVE_KEY: {'loaded' if HIVE_ACTIVE_KEY else 'missing'}). Aborting."
            )
            return 1

        # Instantiate LiquidityPool handler for use with swap, add_liquidity etc.
        lp_handler = LiquidityPool(blockchain_instance=hv)
        # Instantiate nectarengine.Api client for transaction confirmation
        he_api_client = Api(url=HE_API_URL)

        # --- 1. Check Liquidity Pool Price & Potentially Swap TARGET_ASSET for BASE_CURRENCY ---
        # For the target asset, the token pair with the base currency as base is typically 'BASE_CURRENCY:TARGET_ASSET'
        # The fetch_current_pool_price function expects the pair string where the price of the second token (quote) is returned in terms of the first (base).
        # So, for 'BASE_CURRENCY:TARGET_ASSET', it returns BASE_CURRENCY per TARGET_ASSET.
        token_pair_for_swap_and_price_check = (
            f"{args.base_currency}:{args.target_asset}"
        )
        current_pool_price = fetch_current_pool_price(
            token_pair_for_swap_and_price_check, he_api_client=he_api_client
        )

        if current_pool_price is None or current_pool_price <= Decimal(0):
            logging.error(
                f"Could not determine current pool price for {token_pair_for_swap_and_price_check}, or price is zero. Exiting."
            )
            return 1

        logging.info(
            f"Current pool price for {args.target_asset} in {token_pair_for_swap_and_price_check} LP: {current_pool_price:.{target_asset_precision + 2}f} {args.base_currency} per {args.target_asset}."
        )

        swap_hive_received = Decimal("0")  # Initialize
        target_asset_swapped_successfully = False  # Initialize flag      # Check TARGET_ASSET balance before attempting swap
        can_swap_due_to_balance = False
        try:
            wallet = Wallet(account_name, api=he_api_client)
            _token_data_swap = wallet.get_token(args.target_asset)
            if _token_data_swap and "balance" in _token_data_swap:
                current_target_asset_balance = Decimal(_token_data_swap["balance"])
            else:
                current_target_asset_balance = Decimal("0")
                logging.warning(
                    f"{args.target_asset} not found in wallet for {account_name} or balance data missing during swap check. Assuming 0 balance."
                )
            logging.info(
                f"Current {args.target_asset} balance for {account_name}: {current_target_asset_balance}"
            )
            if current_target_asset_balance >= amount_to_trade:
                can_swap_due_to_balance = True
            else:
                logging.warning(
                    f"Insufficient {args.target_asset} balance to swap. Have: {current_target_asset_balance}, Need: {amount_to_trade}. Skipping swap attempt."
                )
        except TokenNotInWallet:
            logging.error(
                f"Account {account_name} does not have any {args.target_asset} tokens in wallet (or token doesn't exist). Skipping swap attempt."
            )
        except Exception as e_wallet_check:
            logging.error(
                f"Error checking {args.target_asset} balance for {account_name}: {e_wallet_check}. Skipping swap attempt."
            )

        if can_swap_due_to_balance and current_pool_price > price_threshold:
            logging.info(
                f"Pool price ({current_pool_price:.{target_asset_precision + 2}f}) > threshold ({price_threshold:.{target_asset_precision + 2}f}). Proceeding with swap."
            )

            sim_to_swap_str = format_amount(amount_to_trade, target_asset_precision)
            logging.info(
                f"Attempting to swap {sim_to_swap_str} {args.target_asset} for {args.base_currency} via {token_pair_for_swap_and_price_check} LP."
            )

            # Calculate expected SWAP.HIVE out (ideal, before fees/slippage)
            expected_swap_hive_out = amount_to_trade * current_pool_price
            # For minAmountOut, apply a slippage tolerance, e.g., 1% to 5%
            # slippage_tolerance = Decimal('0.01') # 1% slippage
            # min_amount_out_decimal = expected_swap_hive_out * (Decimal('1') - slippage_tolerance)
            # min_amount_out_str = format_amount(min_amount_out_decimal, base_currency_precision)
            # For now, we'll just log the expected amount and simulate for dry run.

            # Define slippage tolerance, e.g., 1% (0.01) to 5% (0.05)
            slippage_tolerance = Decimal(
                os.getenv("SLIPPAGE_TOLERANCE", "0.01")
            )  # Default to 1% if not set in .env
            min_amount_out_decimal = expected_swap_hive_out * (
                Decimal("1") - slippage_tolerance
            )
            min_amount_out_str = format_amount(
                min_amount_out_decimal, base_currency_precision
            )

            if not args.dry_run:
                try:
                    logging.info(
                        f"Attempting to swap {sim_to_swap_str} {args.target_asset} for {args.base_currency} in pair {token_pair_for_swap_and_price_check} with min out {min_amount_out_str} {args.base_currency}."
                    )
                    broadcast_receipt = lp_handler.swap_tokens(
                        account=account_name,
                        token_pair=token_pair_for_swap_and_price_check,
                        token_symbol=args.target_asset,
                        token_amount=amount_to_trade,  # nectarengine will handle quantization
                        trade_type="exactInput",
                        min_amount_out=min_amount_out_decimal,  # nectarengine will handle string conversion
                    )
                    logging.info(
                        f"lp_handler.swap_tokens broadcast receipt: {broadcast_receipt}"
                    )

                    target_asset_swapped_successfully = False
                    swap_hive_received = Decimal("0")
                    transaction_id = None

                    if broadcast_receipt and isinstance(broadcast_receipt, dict):
                        logging.debug(
                            f"Attempting to extract transaction_id from receipt: {broadcast_receipt}"
                        )
                        transaction_id = broadcast_receipt.get(
                            "trx_id"
                        )  # Check for 'trx_id' first
                        if not transaction_id:
                            transaction_id = broadcast_receipt.get(
                                "id"
                            )  # Then check for 'id'
                        if (
                            not transaction_id
                            and "result" in broadcast_receipt
                            and isinstance(broadcast_receipt["result"], dict)
                        ):
                            transaction_id = broadcast_receipt["result"].get(
                                "id"
                            )  # Fallback for other structures

                    if not transaction_id:
                        logging.error(
                            "Failed to get transaction_id from broadcast receipt. Cannot confirm swap status."
                        )
                        logging.debug(
                            f"Full broadcast receipt was: {broadcast_receipt}"
                        )
                    else:
                        logging.info(
                            f"Swap transaction broadcasted with ID: {transaction_id}. Waiting {HIVE_ENGINE_TX_CONFIRM_DELAY_SECONDS}s for initial HE confirmation..."
                        )
                        # Confirm transaction using local pizza.py function
                        try:
                            # Local confirm_hive_engine_transaction uses global defaults for delays/retries
                            confirmed_tx_info = confirm_hive_engine_transaction(
                                he_api_client,  # Pass the nectarengine.Api client instance
                                transaction_id,
                            )
                            logging.info(
                                f"Transaction {transaction_id} confirmed by Hive Engine via local confirm_hive_engine_transaction."
                            )
                            tx_info = confirmed_tx_info  # Use this for subsequent original parsing logic
                            # target_asset_swapped_successfully will be determined by event parsing in the 'else' block below.
                        except TransactionConfirmationError as e_confirm:
                            logging.error(
                                f"Transaction {transaction_id} failed confirmation via local confirm_hive_engine_transaction: {e_confirm}"
                            )
                            tx_info = None  # Signal failure to subsequent logic
                            target_asset_swapped_successfully = False
                            swap_hive_received = Decimal("0")

                        # The original script's 'else:' block (for when tx_info and logs exist)
                        # will now execute if tx_info is not None (i.e., local confirmation was successful).
                        # If tx_info is None, that 'else:' block will be skipped, and the failure flags
                        # (target_asset_swapped_successfully, swap_hive_received) are already set from the except block.
                        else:
                            he_logs_str = tx_info["logs"]
                            transaction_had_error = (
                                False  # Reset for this transaction attempt
                            )

                            # Check for errors in HE logs string
                            if isinstance(he_logs_str, str) and he_logs_str.strip():
                                try:
                                    logs_data = json.loads(he_logs_str)
                                    if (
                                        isinstance(logs_data, dict)
                                        and "errors" in logs_data
                                        and logs_data["errors"]
                                    ):
                                        logging.error(
                                            f"Swap transaction {transaction_id} failed with errors from HE logs: {logs_data['errors']}"
                                        )
                                        transaction_had_error = True
                                except json.JSONDecodeError:
                                    logging.warning(
                                        f"Could not JSON decode HE logs string for error check in {transaction_id}: {he_logs_str}"
                                    )
                                    transaction_had_error = (
                                        True  # Treat as error if logs are unparseable
                                    )
                            elif (
                                he_logs_str is not None
                            ):  # Logs exist but not a string, or empty string
                                logging.warning(
                                    f"HE logs for {transaction_id} are not a non-empty string: '{he_logs_str}'. Cannot parse for errors/events."
                                )
                                transaction_had_error = (
                                    True  # Treat as error if logs are malformed
                                )
                            # If he_logs_str is None, it implies no logs/errors, but also no events. Handled by event parsing logic.

                            # Initialize/reset before attempting to parse events and determine success
                            current_swap_actual_hive_received = Decimal("0")

                            if not transaction_had_error:
                                if isinstance(he_logs_str, str):
                                    try:
                                        # he_logs_str should contain the JSON string of logs
                                        logs_data_events = json.loads(he_logs_str)
                                        if (
                                            isinstance(logs_data_events, dict)
                                            and "events" in logs_data_events
                                        ):
                                            for event_item in logs_data_events.get(
                                                "events", []
                                            ):
                                                if (
                                                    event_item.get("contract")
                                                    == "tokens"
                                                    and event_item.get("event")
                                                    == "transferFromContract"
                                                ):
                                                    event_data = event_item.get(
                                                        "data", {}
                                                    )
                                                    if (
                                                        event_data.get("from")
                                                        == "marketpools"
                                                        and event_data.get("to")
                                                        == HIVE_ACCOUNT_NAME
                                                        and event_data.get("symbol")
                                                        == args.base_currency
                                                    ):
                                                        quantity_received_str = (
                                                            event_data.get("quantity")
                                                        )
                                                        if quantity_received_str:
                                                            current_swap_actual_hive_received = Decimal(
                                                                quantity_received_str
                                                            )
                                                            logging.info(
                                                                f"Swap successful! Received {current_swap_actual_hive_received} {args.base_currency} from TX: {transaction_id} (via transferFromContract event)."
                                                            )

                                                            # Log the fee paid from marketpools.swapTokens event
                                                            for (
                                                                fee_event_item
                                                            ) in logs_data_events.get(
                                                                "events", []
                                                            ):
                                                                if (
                                                                    fee_event_item.get(
                                                                        "contract"
                                                                    )
                                                                    == "marketpools"
                                                                    and fee_event_item.get(
                                                                        "event"
                                                                    )
                                                                    == "swapTokens"
                                                                ):
                                                                    fee_data = fee_event_item.get(
                                                                        "data", {}
                                                                    ).get("fee", {})
                                                                    fee_amount = (
                                                                        fee_data.get(
                                                                            "amount"
                                                                        )
                                                                    )
                                                                    fee_symbol = (
                                                                        fee_data.get(
                                                                            "symbol"
                                                                        )
                                                                    )
                                                                    if (
                                                                        fee_amount
                                                                        and fee_symbol
                                                                    ):
                                                                        logging.info(
                                                                            f"Swap fee paid: {fee_amount} {fee_symbol} (from marketpools.swapTokens event)."
                                                                        )
                                                                    break  # Found fee event
                                                            break  # Found primary transferFromContract event
                                    except json.JSONDecodeError:
                                        logging.warning(
                                            f"Could not JSON decode HE logs string for event parsing in {transaction_id}: {he_logs_str}"
                                        )
                                    except Exception as e_event_parse:
                                        logging.error(
                                            f"Error parsing events from HE logs for {transaction_id}: {e_event_parse}"
                                        )

                                # Determine overall swap success based on current_swap_actual_hive_received
                                if current_swap_actual_hive_received > Decimal("0"):
                                    target_asset_swapped_successfully = True
                                    swap_hive_received = current_swap_actual_hive_received  # Update the main variable for later use
                                    logging.info(
                                        f"Swap confirmed for TX: {transaction_id}. Actual {args.base_currency} for LP deposit: {swap_hive_received:.{base_currency_precision}f}."
                                    )
                                else:
                                    # This case: no transaction_had_error, but no valid transferFromContract event found or amount was zero.
                                    target_asset_swapped_successfully = False
                                    swap_hive_received = Decimal(
                                        "0"
                                    )  # Ensure it's zero for safety for subsequent LP deposit logic
                                    logging.error(
                                        f"Swap TX: {transaction_id} had no errors, but required confirmation event (transferFromContract for {args.base_currency} from marketpools) not found or amount was zero. Cannot confirm {args.base_currency} received."
                                    )
                            else:  # transaction_had_error was True
                                target_asset_swapped_successfully = False
                                swap_hive_received = Decimal("0")  # Ensure it's zero
                                logging.error(
                                    f"Swap TX: {transaction_id} failed due to errors reported in HE logs. {args.base_currency} received set to 0."
                                )
                except Exception:
                    logging.exception(
                        "Error during swapTokens broadcast or confirmation process:"
                    )
                    target_asset_swapped_successfully = False
            else:
                swap_hive_received = expected_swap_hive_out.quantize(
                    Decimal(f"1e-{base_currency_precision}"), ROUND_DOWN
                )
                logging.warning(
                    f"[DRY RUN] Would swap {sim_to_swap_str} {args.target_asset} for an estimated {swap_hive_received:.{base_currency_precision}f} {args.base_currency} (min out: {min_amount_out_str} {args.base_currency})."
                )
                logging.info(
                    f"[DRY RUN] Swap payload would be: contract='marketpools', action='swapTokens', payload={{'tokenPair': '{token_pair_for_swap_and_price_check}', 'tokenSymbol': '{args.target_asset}', 'tokenQuantity': '{sim_to_swap_str}', 'minAmountOut': '{min_amount_out_str}'}}"
                )
                target_asset_swapped_successfully = (
                    True  # Assume success for dry run to test next step
                )

            if target_asset_swapped_successfully:
                logging.info(
                    f"Estimated {swap_hive_received:.{base_currency_precision}f} {args.base_currency} to be received from swap operation."
                )
            else:
                logging.error(
                    f"Swap operation for {args.target_asset} was not successful or resulted in zero {args.base_currency}. Skipping LP deposit."
                )

        else:
            logging.info(
                f"Pool price ({current_pool_price:.{target_asset_precision + 2}f}) is not above threshold ({price_threshold:.{base_currency_precision}f}). No swap action taken."
            )

        # --- 2. Add to Liquidity Pool (if TARGET_ASSET was swapped successfully) ---
        if target_asset_swapped_successfully and swap_hive_received > Decimal(0):
            logging.info(
                f"Proceeding to add liquidity with {swap_hive_received:.{base_currency_precision}f} {args.base_currency}."
            )
            # The token_pair_lp for adding liquidity should be the same as used for the swap, or as desired.
            token_pair_lp_deposit = token_pair_for_swap_and_price_check

            swap_hive_to_deposit_for_lp = swap_hive_received
            swap_hive_lp_amount_str = format_amount(
                swap_hive_to_deposit_for_lp, base_currency_precision
            )

            target_asset_lp_amount_str_adjusted = None
            price_for_lp_logging = None  # For the logging statement that follows
            can_add_liquidity = False  # Flag to control if we proceed to actual deposit

            logging.info(
                f"Fetching full pool details for {token_pair_lp_deposit} to calculate exact deposit ratio..."
            )
            actual_pool_data = None
            try:
                # Pool.__init__ raises PoolDoesNotExist if the pool is not found.
                # Otherwise, pool_obj is the dictionary with pool data.
                # Ensure he_api_client is passed to the Pool constructor.
                pool_obj = Pool(token_pair_lp_deposit, api=he_api_client)
                actual_pool_data = dict(pool_obj)  # Get the data from the Pool object
                logging.debug(f"Fetched pool details: {actual_pool_data}")
            except PoolDoesNotExist:
                logging.warning(
                    f"Pool {token_pair_lp_deposit} does not exist. Cannot calculate deposit ratio."
                )
                # actual_pool_data remains None
            except (
                Exception
            ) as e_unexpected_pool_fetch:  # Catch other unexpected errors
                logging.error(
                    f"Unexpected error fetching/processing pool details for {token_pair_lp_deposit}: {e_unexpected_pool_fetch}"
                )
                # actual_pool_data remains None

            if not actual_pool_data:
                # Error already logged by the try/except block or if pool_obj.info was empty.
                # The original script's logic will skip the deposit if actual_pool_data is None.
                logging.info(
                    f"Aborting LP deposit for {token_pair_lp_deposit} due to missing pool data after attempt."
                )
                pass
            else:
                pool_base_quantity_str = actual_pool_data.get(
                    "baseQuantity"
                )  # SWAP.HIVE reserves
                pool_quote_quantity_str = actual_pool_data.get(
                    "quoteQuantity"
                )  # TARGET_ASSET reserves
                price_str_from_pool_obj = actual_pool_data.get(
                    "quotePrice"
                )  # BASE_CURRENCY per TARGET_ASSET, for logging

                if (
                    not pool_base_quantity_str
                    or not pool_quote_quantity_str
                    or not price_str_from_pool_obj
                ):
                    logging.error(
                        f"Pool details for {token_pair_lp_deposit} are incomplete (missing base/quote quantity or price). Pool: {actual_pool_data}. Aborting LP deposit."
                    )
                else:
                    try:
                        pool_base_reserve = Decimal(pool_base_quantity_str)
                        pool_quote_reserve = Decimal(pool_quote_quantity_str)
                        price_for_lp_logging = Decimal(price_str_from_pool_obj)

                        if pool_base_reserve <= Decimal(0):
                            logging.error(
                                f"Pool {token_pair_lp_deposit} has zero or negative base quantity ({pool_base_reserve} {args.base_currency}). Cannot calculate deposit ratio. Aborting LP deposit."
                            )
                        elif pool_quote_reserve < Decimal(
                            0
                        ):  # Quote can be zero (e.g. new pool) but not negative
                            logging.error(
                                f"Pool {token_pair_lp_deposit} has negative quote quantity ({pool_quote_reserve} {args.target_asset}). Cannot calculate deposit ratio. Aborting LP deposit."
                            )
                        else:
                            # We are depositing swap_hive_to_deposit_for_lp (args.base_currency - base token of the pair)
                            # Calculate required args.target_asset (quote token of the pair) based on current reserve ratio
                            # quote_to_add = base_to_add * (quote_reserve / base_reserve)
                            # Use the actual SWAP.HIVE amount that will be in the transaction string for ratio calculation
                            actual_swap_hive_for_ratio_calc = Decimal(
                                swap_hive_lp_amount_str
                            )
                            sim_to_deposit_ideal = actual_swap_hive_for_ratio_calc * (
                                pool_quote_reserve / pool_base_reserve
                            )
                            sim_to_deposit_quantized = sim_to_deposit_ideal.quantize(
                                Decimal(f"1e-{target_asset_precision}"), ROUND_DOWN
                            )
                            target_asset_lp_amount_str_adjusted = format_amount(
                                sim_to_deposit_quantized, target_asset_precision
                            )
                            can_add_liquidity = True  # Calculation successful
                            logging.info(
                                f"Calculated {args.target_asset} to deposit based on reserves: {target_asset_lp_amount_str_adjusted} {args.target_asset} for {swap_hive_lp_amount_str} {args.base_currency}"
                            )

                    except (
                        ZeroDivisionError
                    ):  # Should be caught by pool_base_reserve <= 0, but as safeguard
                        logging.error(
                            f"Division by zero error calculating LP deposit amounts for {token_pair_lp_deposit} (base reserve likely zero). Pool details: {actual_pool_data}"
                        )
                    except (TypeError, ValueError) as e:
                        logging.error(
                            f"Error converting pool reserve or price to Decimal for {token_pair_lp_deposit}. Details: {actual_pool_data}. Error: {e}. Aborting LP deposit."
                        )
                    except Exception as e_calc:
                        logging.error(
                            f"Unexpected error calculating deposit amounts from pool reserves for {token_pair_lp_deposit}. Error: {e_calc}. Aborting LP deposit."
                        )

            if (
                can_add_liquidity
                and target_asset_lp_amount_str_adjusted is not None
                and swap_hive_lp_amount_str is not None
            ):
                logging.info(
                    f"Adjusting LP deposit for {token_pair_lp_deposit} based on pool price ({price_for_lp_logging:.{target_asset_precision + 2}f} {args.base_currency}/{args.target_asset}):"
                )
                logging.info(
                    f"  {args.target_asset} (Quote): {target_asset_lp_amount_str_adjusted}"
                )
                logging.info(
                    f"  {args.base_currency} (Base): {swap_hive_lp_amount_str}"
                )

                # Check balances before attempting to add liquidity
                sufficient_funds_for_lp = False
                try:
                    wallet = Wallet(account_name, api=he_api_client)
                    base_token_to_deposit = Decimal(swap_hive_lp_amount_str)
                    _token_data_base_lp = wallet.get_token(args.base_currency)
                    if _token_data_base_lp and "balance" in _token_data_base_lp:
                        current_base_balance = Decimal(_token_data_base_lp["balance"])
                    else:
                        current_base_balance = Decimal("0")
                        logging.warning(
                            f"{args.base_currency} not found in wallet for {account_name} or balance data missing during LP check. Assuming 0 balance."
                        )
                    logging.info(
                        f"Current {args.base_currency} balance for LP: {current_base_balance}"
                    )

                    if current_base_balance < base_token_to_deposit:
                        logging.error(
                            f"Insufficient {args.base_currency} balance for LP. Have: {current_base_balance}, Need: {base_token_to_deposit}"
                        )
                        # Optionally, raise InsufficientTokenAmount or just let sufficient_funds_for_lp stay False
                    else:
                        # Check args.target_asset balance only if base balance is sufficient
                        quote_token_to_deposit = sim_to_deposit_quantized
                        _token_data_quote_lp = wallet.get_token(args.target_asset)
                        if _token_data_quote_lp and "balance" in _token_data_quote_lp:
                            current_quote_balance = Decimal(
                                _token_data_quote_lp["balance"]
                            )
                        else:
                            current_quote_balance = Decimal("0")
                            logging.warning(
                                f"{args.target_asset} not found in wallet for {account_name} or balance data missing during LP check. Assuming 0 balance."
                            )
                        logging.info(
                            f"Current {args.target_asset} balance for LP: {current_quote_balance}"
                        )

                        if current_quote_balance < quote_token_to_deposit:
                            logging.error(
                                f"Insufficient {args.target_asset} balance for LP. Have: {current_quote_balance}, Need: {quote_token_to_deposit}"
                            )
                        else:
                            sufficient_funds_for_lp = True

                except TokenNotInWallet as e_tnw:
                    logging.error(
                        f"Token not in wallet for LP deposit: {e_tnw}. Aborting LP deposit."
                    )
                except InsufficientTokenAmount as e_ita:  # This custom exception might not be raised by get_token_balance directly
                    logging.warning(
                        f"LP deposit aborted due to insufficient funds reported: {e_ita}"
                    )
                except Exception as e_lp_wallet_check:
                    logging.error(
                        f"Error checking token balances for LP deposit: {e_lp_wallet_check}. Aborting LP deposit."
                    )

                if sufficient_funds_for_lp:
                    if (
                        not args.dry_run
                    ):  # This line was the direct cause of the syntax error
                        # The rest of the block should follow with this new base indentation
                        lp_transaction_id = None
                        lp_deposit_confirmed_successful = False
                        try:
                            logging.info(
                                f"Attempting to add liquidity for {token_pair_lp_deposit} with {swap_hive_lp_amount_str} {args.base_currency} and {target_asset_lp_amount_str_adjusted} {args.target_asset}."
                            )
                            tx_lp_receipt = lp_handler.add_liquidity(
                                account=account_name,
                                token_pair=token_pair_lp_deposit,
                                base_quantity=swap_hive_to_deposit_for_lp,  # nectarengine handles Decimal to str
                                quote_quantity=sim_to_deposit_quantized,  # nectarengine handles Decimal to str
                            )
                            logging.info(
                                f"lp_handler.add_liquidity broadcast receipt: {tx_lp_receipt}"
                            )

                            if tx_lp_receipt and "trx_id" in tx_lp_receipt:
                                lp_transaction_id = tx_lp_receipt["trx_id"]
                                logging.info(
                                    f"Liquidity deposit transaction broadcasted with ID: {lp_transaction_id}. Waiting {HIVE_ENGINE_TX_CONFIRM_DELAY_SECONDS}s for initial HE confirmation..."
                                )
                                # Confirm LP transaction using local pizza.py function
                                try:
                                    # Local confirm_hive_engine_transaction uses global defaults for delays/retries
                                    confirmed_lp_tx_info = confirm_hive_engine_transaction(
                                        he_api_client,  # Pass the nectarengine.Api client instance
                                        lp_transaction_id,
                                    )
                                    logging.info(
                                        f"LP Transaction {lp_transaction_id} confirmed by Hive Engine via local confirm_hive_engine_transaction."
                                    )
                                    lp_tx_info = confirmed_lp_tx_info
                                except TransactionConfirmationError as e_confirm_lp:
                                    logging.error(
                                        f"LP Transaction {lp_transaction_id} failed confirmation via local confirm_hive_engine_transaction: {e_confirm_lp}"
                                    )
                                    lp_tx_info = None  # Signal failure

                                # The original script's 'else:' block (for when lp_tx_info and logs exist)
                                # will now execute if lp_tx_info is not None.
                                else:
                                    lp_he_logs_str = lp_tx_info["logs"]
                                    lp_transaction_had_error = False

                                    if (
                                        isinstance(lp_he_logs_str, str)
                                        and lp_he_logs_str.strip()
                                    ):
                                        try:
                                            lp_logs_data = json.loads(lp_he_logs_str)
                                            if (
                                                isinstance(lp_logs_data, dict)
                                                and "errors" in lp_logs_data
                                                and lp_logs_data["errors"]
                                            ):
                                                logging.error(
                                                    f"LP deposit transaction {lp_transaction_id} failed with errors from HE logs: {lp_logs_data['errors']}"
                                                )
                                                lp_transaction_had_error = True
                                        except json.JSONDecodeError:
                                            logging.warning(
                                                f"Could not JSON decode HE logs string for LP deposit {lp_transaction_id}: {lp_he_logs_str}"
                                            )
                                            lp_transaction_had_error = True
                                    elif (
                                        lp_he_logs_str is not None
                                    ):  # Logs exist but not a string, or empty string
                                        logging.warning(
                                            f"HE logs for LP deposit {lp_transaction_id} are not a non-empty string: '{lp_he_logs_str}'. Cannot parse for errors/events."
                                        )
                                        lp_transaction_had_error = True
                                    # If lp_he_logs_str is None, implies no logs/errors, but also no events.

                                    if not lp_transaction_had_error:
                                        if (
                                            isinstance(lp_he_logs_str, str)
                                            and lp_he_logs_str.strip()
                                        ):  # Re-check for safety before parsing events
                                            try:
                                                lp_logs_data_events = json.loads(
                                                    lp_he_logs_str
                                                )
                                                if (
                                                    isinstance(
                                                        lp_logs_data_events, dict
                                                    )
                                                    and "events" in lp_logs_data_events
                                                ):
                                                    for (
                                                        event_item
                                                    ) in lp_logs_data_events.get(
                                                        "events", []
                                                    ):
                                                        if (
                                                            event_item.get("contract")
                                                            == "marketpools"
                                                            and event_item.get("event")
                                                            == "addLiquidity"
                                                        ):
                                                            event_data = event_item.get(
                                                                "data", {}
                                                            )
                                                            # For addLiquidity, the event itself is confirmation. Specific amounts can be logged if needed.
                                                            logging.info(
                                                                f"Liquidity successfully added for TX: {lp_transaction_id}. Event data: {event_data}"
                                                            )
                                                            lp_deposit_confirmed_successful = True
                                                            break  # Found addLiquidity event
                                                    if not lp_deposit_confirmed_successful:
                                                        logging.error(
                                                            f"LP deposit TX {lp_transaction_id} had no errors, but 'marketpools.addLiquidity' event not found in logs. Logs: {lp_he_logs_str}"
                                                        )
                                                else:
                                                    logging.error(
                                                        f"LP deposit TX {lp_transaction_id} logs do not contain 'events' array or are not a dict. Logs: {lp_he_logs_str}"
                                                    )
                                            except json.JSONDecodeError:
                                                logging.warning(
                                                    f"Could not JSON decode HE logs string for LP event parsing in {lp_transaction_id}: {lp_he_logs_str}"
                                                )
                                            except Exception as e_lp_event_parse:
                                                logging.error(
                                                    f"Error parsing events from HE logs for LP deposit {lp_transaction_id}: {e_lp_event_parse}"
                                                )
                                        else:
                                            # No HE logs string or empty, but no error reported earlier. This means no events either.
                                            logging.error(
                                                f"LP deposit TX {lp_transaction_id} had no errors, but HE logs string was empty or None. Cannot confirm 'marketpools.addLiquidity' event."
                                            )
                                    # else: lp_transaction_had_error was True, error already loggedpt: {tx_lp_receipt}")
                        except Exception as e_broadcast_lp:
                            logging.exception(
                                f"Error broadcasting addLiquidity for {token_pair_lp_deposit}: {e_broadcast_lp}"
                            )

                        if lp_deposit_confirmed_successful:
                            logging.info(
                                f"Liquidity successfully added to {token_pair_lp_deposit} (TX: {lp_transaction_id})."
                            )
                        else:
                            logging.error(
                                f"Failed to confirm successful liquidity deposit to {token_pair_lp_deposit}. Review logs for TX ID {lp_transaction_id if lp_transaction_id else 'N/A'}."
                            )
                    else:
                        logging.warning(
                            f"[DRY RUN] Would add {target_asset_lp_amount_str_adjusted} {args.target_asset} and {swap_hive_lp_amount_str} {args.base_currency} to LP {token_pair_lp_deposit}."
                        )
            else:
                logging.info(
                    f"Skipping LP deposit for {token_pair_lp_deposit} because amounts could not be calculated/validated, or one of the amounts is zero/None ({args.target_asset}: {target_asset_lp_amount_str_adjusted}, {args.base_currency}: {swap_hive_lp_amount_str})."
                )
        elif target_asset_swapped_successfully and swap_hive_received <= Decimal(0):
            logging.info(
                f"Swap was marked successful, but {args.base_currency} received is zero. Skipping LP deposit."
            )
        else:
            logging.info(
                f"{args.target_asset} was not swapped, or swap was not successful. Skipping LP deposit."
            )

        logging.info("Operations completed.")

    except Exception as e:
        logging.exception(
            f"An unexpected error occurred in main: {e}"
        )  # Changed to logging.exception for full traceback
        return 1

    return 0


if __name__ == "__main__":
    exit_code = main()
    if exit_code == 0:
        logging.info("Script finished successfully.")
    else:
        logging.error(f"Script finished with error code {exit_code}.")
    sys.exit(exit_code)  # Ensure cron gets an exit code
