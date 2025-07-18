import os
import time
import json
import streamlit as st
import requests
from solana.rpc.api import Client
from solders.keypair import Keypair
from solders.pubkey import Pubkey as PublicKey
from solders.transaction import Transaction
from solana.rpc.types import TxOpts
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
from dotenv import load_dotenv
from typing import Dict, List, Optional
import base64
import base58

# Load environment variables
load_dotenv()

# Configuration
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Can be mnemonic phrase or base58 private key
TARGET_WALLET = os.getenv("TARGET_WALLET")
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
JUPITER_API_URL = "https://quote-api.jup.ag/v6"

# Trading Configuration
BUY_AMOUNT_SOL = 0.03  # Fixed buy amount in SOL
BUY_AMOUNT_LAMPORTS = int(BUY_AMOUNT_SOL * 1_000_000_000)  # Convert to lamports
SOL_MINT = "So11111111111111111111111111111111111111112"  # SOL token address

# Trading Rules
PROFIT_TARGET = 100  # 100% profit to sell 50%
STOP_LOSS_PERCENTAGE = -50  # -50% loss to sell everything

# Position tracking
if 'positions' not in st.session_state:
    st.session_state.positions = {}  # token_address: {amount, entry_price, timestamp}

if 'executed_trades' not in st.session_state:
    st.session_state.executed_trades = set()

if 'trading_active' not in st.session_state:
    st.session_state.trading_active = False

# Wallet setup - IMPROVED
def get_keypair_from_private_key(private_key: str) -> Keypair:
    """Generate Solana keypair from either mnemonic phrase or base58 private key"""
    try:
        # Remove any quotes and whitespace
        clean_private_key = private_key.strip().strip('"\'')
        
        # Check if it's a mnemonic phrase (12 or 24 words)
        words = clean_private_key.split()
        if len(words) in [12, 24]:
            st.info("Detected mnemonic phrase, generating keypair...")
            return get_keypair_from_mnemonic(clean_private_key)
        
        # Check if it's a base58 private key
        elif len(clean_private_key) == 88 or len(clean_private_key) == 87:
            st.info("Detected base58 private key, generating keypair...")
            return get_keypair_from_base58(clean_private_key)
        
        # Check if it's a JSON array format [1,2,3,...]
        elif clean_private_key.startswith('[') and clean_private_key.endswith(']'):
            st.info("Detected JSON array private key, generating keypair...")
            return get_keypair_from_json_array(clean_private_key)
        
        else:
            # Try to parse as base58 anyway
            st.info("Attempting to parse as base58 private key...")
            return get_keypair_from_base58(clean_private_key)
            
    except Exception as e:
        st.error(f"Error creating wallet from private key: {e}")
        st.error("Please check your PRIVATE_KEY format. Supported formats:")
        st.error("1. Mnemonic phrase (12 or 24 words)")
        st.error("2. Base58 private key (87-88 characters)")
        st.error("3. JSON array format [1,2,3,...]")
        st.stop()

def get_keypair_from_mnemonic(mnemonic: str) -> Keypair:
    """Generate Solana keypair from mnemonic using BIP44 derivation"""
    try:
        # Generate seed from mnemonic
        seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
        
        # Use BIP44 derivation path for Solana: m/44'/501'/0'/0'
        bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(0)
        bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
        bip44_addr_ctx = bip44_chg_ctx.AddressIndex(0)
        
        # Get the private key bytes
        private_key_bytes = bip44_addr_ctx.PrivateKey().Raw().ToBytes()
        
        return Keypair.from_bytes(private_key_bytes)
        
    except Exception as e:
        st.error(f"Error creating keypair from mnemonic: {e}")
        raise

def get_keypair_from_base58(base58_key: str) -> Keypair:
    """Generate Solana keypair from base58 private key"""
    try:
        # Decode base58 private key
        private_key_bytes = base58.b58decode(base58_key)
        
        # Solana private keys are 64 bytes (32 bytes secret + 32 bytes public)
        if len(private_key_bytes) == 64:
            return Keypair.from_bytes(private_key_bytes)
        elif len(private_key_bytes) == 32:
            # If only 32 bytes, assume it's just the secret key
            return Keypair.from_seed(private_key_bytes)
        else:
            raise ValueError(f"Invalid private key length: {len(private_key_bytes)} bytes")
            
    except Exception as e:
        st.error(f"Error creating keypair from base58: {e}")
        raise

def get_keypair_from_json_array(json_array: str) -> Keypair:
    """Generate Solana keypair from JSON array format"""
    try:
        # Parse JSON array
        key_array = json.loads(json_array)
        
        # Convert to bytes
        private_key_bytes = bytes(key_array)
        
        # Check length and create keypair
        if len(private_key_bytes) == 64:
            return Keypair.from_bytes(private_key_bytes)
        elif len(private_key_bytes) == 32:
            return Keypair.from_seed(private_key_bytes)
        else:
            raise ValueError(f"Invalid private key length: {len(private_key_bytes)} bytes")
            
    except Exception as e:
        st.error(f"Error creating keypair from JSON array: {e}")
        raise

# Initialize wallet and client
try:
    if not PRIVATE_KEY:
        st.error("PRIVATE_KEY environment variable not set!")
        st.error("Please set your PRIVATE_KEY in the .env file")
        st.stop()
    
    wallet = get_keypair_from_private_key(PRIVATE_KEY)
    client = Client(RPC_URL)
    
    # Verify wallet connection
    wallet_address = str(wallet.pubkey())
    st.success("Wallet initialized successfully!")
    st.info(f"Your wallet address: {wallet_address}")
    
    # Test connection by getting balance
    balance_result = client.get_balance(wallet.pubkey())
    if balance_result.value is not None:
        sol_balance = balance_result.value / 1_000_000_000
        st.info(f"Current balance: {sol_balance:.4f} SOL")
    else:
        st.warning("Could not fetch balance - RPC connection may be slow")
        
except Exception as e:
    st.error("Failed to initialize wallet: {}".format(e))
    st.error("Please check your PRIVATE_KEY and try again")
    st.stop()

# Jupiter API functions
def get_token_price(token_address: str) -> Optional[float]:
    """Get current token price in USD"""
    try:
        response = requests.get(f"{JUPITER_API_URL}/price?ids={token_address}", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('data', {}).get(token_address, {}).get('price', 0)
    except Exception as e:
        st.error(f"Error fetching price for {token_address[:8]}...: {e}")
    return None

def get_jupiter_quote(input_mint: str, output_mint: str, amount: int):
    """Get swap quote from Jupiter"""
    try:
        params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': str(amount),  # Convert to string
            'slippageBps': 300,  # 3% slippage for better execution
            'onlyDirectRoutes': False,
            'asLegacyTransaction': False
        }
        response = requests.get(f"{JUPITER_API_URL}/quote", params=params, timeout=15)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Quote API error: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        st.error(f"Error getting quote: {e}")
        return None

def execute_jupiter_swap(quote_response: dict) -> bool:
    """Execute swap using Jupiter API"""
    try:
        # Get swap transaction
        swap_payload = {
            'quoteResponse': quote_response,
            'userPublicKey': str(wallet.pubkey()),
            'wrapAndUnwrapSol': True,
            'useSharedAccounts': True,
            'feeAccount': None,
            'trackingAccount': None,
            'asLegacyTransaction': False
        }
        
        response = requests.post(f"{JUPITER_API_URL}/swap", json=swap_payload, timeout=20)
        if response.status_code != 200:
            st.error(f"Swap API error: {response.status_code} - {response.text}")
            return False
            
        swap_data = response.json()
        
        if 'swapTransaction' not in swap_data:
            st.error(f"No swap transaction in response: {swap_data}")
            return False
        
        # Deserialize transaction
        transaction_bytes = base64.b64decode(swap_data['swapTransaction'])
        transaction = Transaction.from_bytes(transaction_bytes)
        
        # Sign transaction
        signed_tx = wallet.sign_message(transaction.message.serialize())
        transaction = Transaction.populate(transaction.message, [signed_tx])
        
        # Send transaction with proper options
        opts = TxOpts(skip_preflight=True, max_retries=3)
        result = client.send_transaction(transaction, opts=opts)
        
        if result.value:
            st.success(f"Swap executed: {result.value}")
            return True
        else:
            st.error("Transaction failed - no signature returned")
            return False
            
    except Exception as e:
        st.error(f"Error executing swap: {e}")
        return False

def parse_transaction_for_tokens(tx_signature: str) -> List[Dict]:
    """Parse transaction to extract token swap information"""
    try:
        # Get transaction details
        tx_result = client.get_transaction(
            tx_signature, 
            encoding="jsonParsed",
            max_supported_transaction_version=0
        )
        
        if not tx_result.value:
            return []
        
        tx_data = tx_result.value
        
        # Look for Jupiter/swap instructions
        tokens = []
        instructions = tx_data.transaction.message.instructions
        
        for instruction in instructions:
            # Look for token transfers and swaps
            if hasattr(instruction, 'parsed') and instruction.parsed:
                parsed = instruction.parsed
                if parsed.get('type') == 'transfer':
                    # This is a simplified approach - you'd need more sophisticated parsing
                    # for production use to properly identify swaps vs regular transfers
                    pass
        
        # For demo purposes, we'll simulate finding a token swap
        # In production, you'd need to parse the actual instruction data
        # to extract the exact tokens being swapped
        
        return tokens
    except Exception as e:
        st.error(f"Error parsing transaction {tx_signature[:8]}...: {e}")
        return []

def copy_trade_by_signature(tx_signature: str) -> bool:
    """Copy a trade based on transaction signature - DEMO VERSION"""
    try:
        st.info(f"Analyzing transaction: {tx_signature[:8]}...")
        
        # In a real implementation, you would:
        # 1. Parse the transaction to extract the exact token being bought
        # 2. Get the token address from the transaction data
        # 3. Execute the same trade with your fixed amount
        
        # For demo purposes, let's simulate this with a placeholder token
        # You would replace this with actual parsing logic
        
        st.warning("Demo Mode: This would execute a real trade in production")
        st.info(f"Would buy unknown token with {BUY_AMOUNT_SOL} SOL")
        
        # Simulate successful trade for demo
        return False  # Set to True to simulate successful trades
        
    except Exception as e:
        st.error(f"Error copying trade: {e}")
        return False

def check_and_sell_on_profit():
    """Check positions and sell 50% at profit target"""
    if not st.session_state.positions:
        return
    
    positions_to_update = {}
    
    for token_address, position in st.session_state.positions.items():
        try:
            current_price = get_token_price(token_address)
            
            if current_price and position.get('entry_price'):
                profit_percentage = ((current_price - position['entry_price']) / position['entry_price']) * 100
                
                if profit_percentage >= PROFIT_TARGET:  # Profit target reached
                    st.success("Profit target reached for {}...! (+{:.1f}%)".format(token_address[:8], profit_percentage))
                    
                    # Calculate 50% of position to sell
                    sell_amount = position['amount'] // 2
                    
                    if sell_amount > 0:
                        # Get quote to sell 50% back to SOL
                        quote = get_jupiter_quote(
                            input_mint=token_address,
                            output_mint=SOL_MINT,
                            amount=sell_amount
                        )
                        
                        if quote and execute_jupiter_swap(quote):
                            # Calculate SOL received
                            sol_received = int(quote['outAmount']) / 1_000_000_000
                            
                            # Update position
                            positions_to_update[token_address] = {
                                **position,
                                'amount': position['amount'] - sell_amount
                            }
                            
                            st.success("Sold 50% of {}... for {:.4f} SOL!".format(token_address[:8], sol_received))
                        else:
                            st.error("Failed to execute profit-taking sell for {}...".format(token_address[:8]))
        except Exception as e:
            st.error("Error checking profit for {}...: {}".format(token_address[:8], e))
    
    # Update positions
    for token_address, updated_position in positions_to_update.items():
        if updated_position['amount'] <= 1000:  # Remove very small positions
            del st.session_state.positions[token_address]
            st.info("Position closed for {}... (remaining amount too small)".format(token_address[:8]))
        else:
            st.session_state.positions[token_address] = updated_position

def check_and_execute_stop_loss():
    """Check positions and execute stop-loss"""
    if not st.session_state.positions:
        return
    
    positions_to_remove = []
    
    for token_address, position in st.session_state.positions.items():
        try:
            current_price = get_token_price(token_address)
            
            if current_price and position.get('entry_price'):
                profit_percentage = ((current_price - position['entry_price']) / position['entry_price']) * 100
                
                if profit_percentage <= STOP_LOSS_PERCENTAGE:  # Stop-loss triggered
                    st.error("STOP-LOSS TRIGGERED for {}...! ({:.1f}%)".format(token_address[:8], profit_percentage))
                    
                    # Sell entire position
                    sell_amount = position['amount']
                    
                    # Get quote to sell everything back to SOL
                    quote = get_jupiter_quote(
                        input_mint=token_address,
                        output_mint=SOL_MINT,
                        amount=sell_amount
                    )
                    
                    if quote and execute_jupiter_swap(quote):
                        # Calculate loss
                        sol_received = int(quote['outAmount']) / 1_000_000_000
                        sol_spent = position.get('sol_spent', BUY_AMOUNT_SOL)
                        loss_amount = sol_spent - sol_received
                        
                        st.error("STOP-LOSS EXECUTED: Received {:.4f} SOL (Loss: {:.4f} SOL)".format(sol_received, loss_amount))
                        positions_to_remove.append(token_address)
                    else:
                        st.error("Failed to execute stop-loss for {}...".format(token_address[:8]))
        except Exception as e:
            st.error("Error checking stop-loss for {}...: {}".format(token_address[:8], e))
    
    # Remove stopped-out positions
    for token_address in positions_to_remove:
        if token_address in st.session_state.positions:
            del st.session_state.positions[token_address]

# Streamlit UI
st.set_page_config(page_title="Solana Copy Trading Bot", page_icon="🤖", layout="wide")

st.title("Solana Copy Trading Bot")
st.warning("**LIVE TRADING MODE** - This bot executes real trades with real money!")

# Display configuration in sidebar
with st.sidebar:
    st.header("Configuration")
    st.info("**Buy Amount:** {} SOL".format(BUY_AMOUNT_SOL))
    st.success("**Profit Target:** +{}%".format(PROFIT_TARGET))
    st.error("**Stop-Loss:** {}%".format(STOP_LOSS_PERCENTAGE))
    
    st.header("Connection")
    st.code("Your Wallet: {}...".format(str(wallet.pubkey())[:8]), language=None)
    if TARGET_WALLET:
        st.code("Target Wallet: {}...".format(TARGET_WALLET[:8]), language=None)
    else:
        st.error("TARGET_WALLET not set!")

# Main dashboard
col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    st.subheader("Account Balance")
    try:
        balance_result = client.get_balance(wallet.pubkey())
        if balance_result.value is not None:
            sol_balance = balance_result.value / 1_000_000_000
            st.metric("SOL Balance", "{:.4f} SOL".format(sol_balance))
            
            if sol_balance < BUY_AMOUNT_SOL:
                st.error("Insufficient balance! Need at least {} SOL".format(BUY_AMOUNT_SOL))
        else:
            st.error("Could not fetch balance")
    except Exception as e:
        st.error("Error checking balance: {}".format(e))

with col2:
    st.subheader("Trading Status")
    status = "ACTIVE" if st.session_state.trading_active else "STOPPED"
    st.metric("Bot Status", status)
    
    if st.session_state.positions:
        st.metric("Open Positions", len(st.session_state.positions))
    else:
        st.metric("Open Positions", "0")

with col3:
    st.subheader("Controls")
    if st.button("START", type="primary", use_container_width=True):
        if not TARGET_WALLET:
            st.error("Please set TARGET_WALLET in environment variables!")
        else:
            st.session_state.trading_active = True
            st.success("Bot started!")
            st.rerun()
    
    if st.button("STOP", type="secondary", use_container_width=True):
        st.session_state.trading_active = False
        st.error("Bot stopped!")
        st.rerun()

# Positions display
st.subheader("Current Positions")
if st.session_state.positions:
    for token_address, position in st.session_state.positions.items():
        with st.expander("{} - {} SOL invested".format(token_address[:8], position.get('sol_spent', BUY_AMOUNT_SOL))):
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.write("**Amount:** {:,}".format(position['amount']))
                st.write("**Entry Price:** ${:.8f}".format(position.get('entry_price', 0)))
            
            with col2:
                current_price = get_token_price(token_address)
                if current_price and position.get('entry_price'):
                    profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
                    st.write("**Current Price:** ${:.8f}".format(current_price))
                    
                    if profit_pct >= PROFIT_TARGET:
                        st.success("**P&L:** +{:.1f}%".format(profit_pct))
                    elif profit_pct > 0:
                        st.info("**P&L:** +{:.1f}%".format(profit_pct))
                    elif profit_pct <= STOP_LOSS_PERCENTAGE:
                        st.error("**P&L:** {:.1f}%".format(profit_pct))
                    else:
                        st.warning("**P&L:** {:.1f}%".format(profit_pct))
                else:
                    st.write("**Current Price:** Loading...")
                    st.write("**P&L:** Calculating...")
            
            with col3:
                entry_time = position.get('timestamp', time.time())
                duration = time.time() - entry_time
                hours = int(duration // 3600)
                minutes = int((duration % 3600) // 60)
                st.write("**Duration:** {}h {}m".format(hours, minutes))
                
                if st.button("Close Position", key="close_{}".format(token_address)):
                    # Manual close position logic would go here
                    st.info("Manual close feature would be implemented here")
else:
    st.info("No open positions")

# Trading loop
if st.session_state.trading_active and TARGET_WALLET:
    st.subheader("Live Trading Feed")
    
    with st.container():
        try:
            # Fetch recent transactions from target wallet
            result = client.get_signatures_for_address(
                PublicKey(TARGET_WALLET), 
                limit=5
            )
            
            if result.value:
                transactions = result.value
                
                # Process new transactions
                new_trades = 0
                for tx in transactions:
                    if hasattr(tx, 'signature'):
                        sig = tx.signature
                        if sig and sig not in st.session_state.executed_trades:
                            st.info("New transaction detected: {}...".format(sig[:8]))
                            
                            if copy_trade_by_signature(sig):
                                st.session_state.executed_trades.add(sig)
                                new_trades += 1
                                st.success("Trade copied successfully!")
                            else:
                                st.warning("Could not copy trade (demo mode)")
                
                if new_trades == 0:
                    st.success("No new trades to copy")
            else:
                st.warning("No transactions found for target wallet")
            
            # Check positions for profit taking and stop losses
            if st.session_state.positions:
                st.info("Checking positions for profit/loss triggers...")
                check_and_sell_on_profit()
                check_and_execute_stop_loss()
            
        except Exception as e:
            st.error("Error in trading loop: {}".format(e))
    
    # Auto-refresh every 10 seconds when active
    if st.session_state.trading_active:
        time.sleep(2)  # Short delay to prevent too frequent updates
        st.rerun()

else:
    if not TARGET_WALLET:
        st.error("TARGET_WALLET environment variable not set!")
    else:
        st.info("Bot is stopped. Click 'START' to begin trading.")

# Summary statistics
st.subheader("Summary")
col1, col2, col3, col4 = st.columns(4)

with col1:
    total_trades = len(st.session_state.executed_trades)
    st.metric("Total Trades", total_trades)

with col2:
    total_positions = len(st.session_state.positions)
    st.metric("Active Positions", total_positions)

with col3:
    if st.session_state.positions:
        total_invested = sum(pos.get('sol_spent', BUY_AMOUNT_SOL) for pos in st.session_state.positions.values())
        st.metric("Total Invested", "{:.3f} SOL".format(total_invested))
    else:
        st.metric("Total Invested", "0 SOL")

with col4:
    # Calculate approximate P&L
    total_pnl = 0
    for token_address, position in st.session_state.positions.items():
        current_price = get_token_price(token_address)
        if current_price and position.get('entry_price'):
            profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
            sol_invested = position.get('sol_spent', BUY_AMOUNT_SOL)
            pnl_sol = sol_invested * (profit_pct / 100)
            total_pnl += pnl_sol
    
    if total_pnl > 0:
        st.metric("Unrealized P&L", "+{:.4f} SOL".format(total_pnl), delta="+{:.4f}".format(total_pnl))
    elif total_pnl < 0:
        st.metric("Unrealized P&L", "{:.4f} SOL".format(total_pnl), delta="{:.4f}".format(total_pnl))
    else:
        st.metric("Unrealized P&L", "0 SOL")

# Footer
st.markdown("---")
st.caption("**Disclaimer:** This is experimental software. Use at your own risk. Always test with small amounts first.")

# Debug information
with st.expander("Debug Information"):
    st.write("**Environment Variables:**")
    st.write("- PRIVATE_KEY: {}".format('Set' if PRIVATE_KEY else 'Not set'))
    st.write("- TARGET_WALLET: {}".format('Set' if TARGET_WALLET else 'Not set'))
    st.write("- RPC_URL: {}".format(RPC_URL))
    
    st.write("**Wallet Info:**")
    st.write("- Public Key: {}".format(str(wallet.pubkey())))
    st.write("- Network: {}".format('Mainnet' if 'mainnet' in RPC_URL else 'Devnet/Testnet'))
    
    if st.button("Test Connection"):
        try:
            balance_result = client.get_balance(wallet.pubkey())
            if balance_result.value is not None:
                st.success("Connection successful! Balance: {:.4f} SOL".format(balance_result.value / 1_000_000_000))
            else:
                st.error("Connection failed - could not fetch balance")
        except Exception as e:
            st.error("Connection error: {}".format(e))