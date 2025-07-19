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
MNEMONIC = os.getenv("PRIVATE_KEY")  # Your mnemonic phrase
PRIVATE_KEY_BASE58 = os.getenv("PRIVATE_KEY_BASE58")  # Alternative: base58 private key
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

# Wallet setup - FIXED WITH MULTIPLE METHODS
def get_keypair_from_base58(private_key_base58: str) -> Keypair:
    """Generate Solana keypair from base58 private key (most reliable method)"""
    try:
        # Decode base58 private key
        private_key_bytes = base58.b58decode(private_key_base58)
        return Keypair.from_bytes(private_key_bytes)
    except Exception as e:
        st.error(f"Error creating wallet from base58 key: {e}")
        raise

def get_keypair_from_mnemonic_bip44(mnemonic: str, account_index: int = 0) -> Keypair:
    """Generate Solana keypair from mnemonic using proper BIP44 derivation (Solana standard)"""
    try:
        # Remove any quotes from mnemonic
        clean_mnemonic = mnemonic.strip().strip('"\'')
        
        # Generate seed from mnemonic
        seed_bytes = Bip39SeedGenerator(clean_mnemonic).Generate()
        
        # Use BIP44 derivation for Solana (coin type 501)
        # Path: m/44'/501'/0'/0'
        bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(account_index)
        bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
        bip44_addr_ctx = bip44_chg_ctx.AddressIndex(0)
        
        # Get private key (32 bytes)
        private_key_bytes = bip44_addr_ctx.PrivateKey().Raw().ToBytes()[:32]
        
        return Keypair.from_bytes(private_key_bytes)
    except Exception as e:
        st.error(f"Error creating wallet from mnemonic (BIP44): {e}")
        raise

def get_keypair_from_mnemonic_simple(mnemonic: str) -> Keypair:
    """Generate Solana keypair from mnemonic using simple seed approach"""
    try:
        # Remove any quotes from mnemonic
        clean_mnemonic = mnemonic.strip().strip('"\'')
        
        # Generate seed from mnemonic
        seed_bytes = Bip39SeedGenerator(clean_mnemonic).Generate()
        
        # Use first 32 bytes as private key
        private_key = seed_bytes[:32]
        
        return Keypair.from_seed(bytes(private_key))
    except Exception as e:
        st.error(f"Error creating wallet from mnemonic (simple): {e}")
        raise

def initialize_wallet():
    """Initialize wallet using multiple methods"""
    wallet = None
    method_used = ""
    
    # Method 1: Try base58 private key first (most reliable)
    if PRIVATE_KEY_BASE58:
        try:
            wallet = get_keypair_from_base58(PRIVATE_KEY_BASE58)
            method_used = "Base58 Private Key"
            st.info(f"Wallet loaded using {method_used}")
        except Exception as e:
            st.warning(f"Failed to load wallet from base58 key: {e}")
    
    # Method 2: Try BIP44 derivation (Phantom/Solflare standard)
    if not wallet and MNEMONIC:
        try:
            wallet = get_keypair_from_mnemonic_bip44(MNEMONIC)
            method_used = "Mnemonic (BIP44 - Account 0)"
            st.info(f"Wallet loaded using {method_used}")
        except Exception as e:
            st.warning(f"Failed to load wallet from mnemonic (BIP44): {e}")
            
            # Method 3: Try simple seed approach
            try:
                wallet = get_keypair_from_mnemonic_simple(MNEMONIC)
                method_used = "Mnemonic (Simple Seed)"
                st.info(f"Wallet loaded using {method_used}")
            except Exception as e:
                st.error(f"Failed to load wallet from mnemonic (simple): {e}")
    
    if not wallet:
        st.error("Could not initialize wallet with any method!")
        st.error("Please check your PRIVATE_KEY (mnemonic) or PRIVATE_KEY_BASE58 environment variables")
        st.info("**How to get your private key:**")
        st.info("1. **Phantom Wallet**: Settings > Export Private Key (base58 format)")
        st.info("2. **Solflare**: Settings > Export Wallet > Private Key")
        st.info("3. **Command line**: solana-keygen grind or use existing keypair file")
        st.stop()
    
    return wallet, method_used

# Initialize wallet and client
try:
    wallet, wallet_method = initialize_wallet()
    client = Client(RPC_URL)
    st.success(f"Wallet initialized successfully using {wallet_method}!")
    
    # Display wallet info
    wallet_address = str(wallet.pubkey())
    st.info(f"**Wallet Address**: {wallet_address}")
    
    # Verify wallet by checking balance
    try:
        balance_result = client.get_balance(wallet.pubkey())
        if balance_result.value is not None:
            sol_balance = balance_result.value / 1_000_000_000
            st.success(f"**Wallet Balance**: {sol_balance:.6f} SOL")
        else:
            st.warning("Could not verify wallet balance - RPC might be slow")
    except Exception as e:
        st.warning(f"Could not verify wallet balance: {e}")
        
except Exception as e:
    st.error(f"Failed to initialize wallet: {e}")
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
                    st.success(f"Profit target reached for {token_address[:8]}...! (+{profit_percentage:.1f}%)")
                    
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
                            
                            st.success(f"Sold 50% of {token_address[:8]}... for {sol_received:.4f} SOL!")
                        else:
                            st.error(f"Failed to execute profit-taking sell for {token_address[:8]}...")
        except Exception as e:
            st.error(f"Error checking profit for {token_address[:8]}...: {e}")
    
    # Update positions
    for token_address, updated_position in positions_to_update.items():
        if updated_position['amount'] <= 1000:  # Remove very small positions
            del st.session_state.positions[token_address]
            st.info(f"Position closed for {token_address[:8]}... (remaining amount too small)")
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
                    st.error(f"STOP-LOSS TRIGGERED for {token_address[:8]}...! ({profit_percentage:.1f}%)")
                    
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
                        
                        st.error(f"STOP-LOSS EXECUTED: Received {sol_received:.4f} SOL (Loss: {loss_amount:.4f} SOL)")
                        positions_to_remove.append(token_address)
                    else:
                        st.error(f"Failed to execute stop-loss for {token_address[:8]}...")
        except Exception as e:
            st.error(f"Error checking stop-loss for {token_address[:8]}...: {e}")
    
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
    st.info(f"**Buy Amount:** {BUY_AMOUNT_SOL} SOL")
    st.success(f"**Profit Target:** +{PROFIT_TARGET}%")
    st.error(f"**Stop-Loss:** {STOP_LOSS_PERCENTAGE}%")
    
    st.header("Connection")
    st.code(f"Wallet: {str(wallet.pubkey())[:8]}...", language=None)
    st.caption(f"Method: {wallet_method}")
    if TARGET_WALLET:
        st.code(f"Target: {TARGET_WALLET[:8]}...", language=None)
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
            st.metric("SOL Balance", f"{sol_balance:.4f} SOL")
            
            if sol_balance < BUY_AMOUNT_SOL:
                st.error(f"Insufficient balance! Need at least {BUY_AMOUNT_SOL} SOL")
        else:
            st.error("Could not fetch balance")
    except Exception as e:
        st.error(f"Error checking balance: {e}")

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
        with st.expander(f"{token_address[:8]}... - {position.get('sol_spent', BUY_AMOUNT_SOL)} SOL invested"):
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.write(f"**Amount:** {position['amount']:,}")
                st.write(f"**Entry Price:** ${position.get('entry_price', 0):.8f}")
            
            with col2:
                current_price = get_token_price(token_address)
                if current_price and position.get('entry_price'):
                    profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
                    st.write(f"**Current Price:** ${current_price:.8f}")
                    
                    if profit_pct >= PROFIT_TARGET:
                        st.success(f"**P&L:** +{profit_pct:.1f}% (TARGET)")
                    elif profit_pct > 0:
                        st.info(f"**P&L:** +{profit_pct:.1f}%")
                    elif profit_pct <= STOP_LOSS_PERCENTAGE:
                        st.error(f"**P&L:** {profit_pct:.1f}% (STOP-LOSS)")
                    else:
                        st.warning(f"**P&L:** {profit_pct:.1f}%")
                else:
                    st.write("**Current Price:** Loading...")
                    st.write("**P&L:** Calculating...")
            
            with col3:
                entry_time = position.get('timestamp', time.time())
                duration = time.time() - entry_time
                hours = int(duration // 3600)
                minutes = int((duration % 3600) // 60)
                st.write(f"**Duration:** {hours}h {minutes}m")
                
                if st.button(f"Close Position", key=f"close_{token_address}"):
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
                            st.info(f"New transaction detected: {sig[:8]}...")
                            
                            if copy_trade_by_signature(sig):
                                st.session_state.executed_trades.add(sig)
                                new_trades += 1
                                st.success(f"Trade copied successfully!")
                            else:
                                st.warning(f"Could not copy trade (demo mode)")
                
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
            st.error(f"Error in trading loop: {e}")
    
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
        st.metric("Total Invested", f"{total_invested:.3f} SOL")
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
        st.metric("Unrealized P&L", f"+{total_pnl:.4f} SOL", delta=f"+{total_pnl:.4f}")
    elif total_pnl < 0:
        st.metric("Unrealized P&L", f"{total_pnl:.4f} SOL", delta=f"{total_pnl:.4f}")
    else:
        st.metric("Unrealized P&L", "0 SOL")

# Footer
st.markdown("---")
st.caption("**Disclaimer:** This is experimental software. Use at your own risk. Always test with small amounts first.")

# Debug section (expandable)
with st.expander("Debug Information"):
    st.write("**Environment Variables Status:**")
    st.write(f"- MNEMONIC set: {'Yes' if MNEMONIC else 'No'}")
    st.write(f"- PRIVATE_KEY_BASE58 set: {'Yes' if PRIVATE_KEY_BASE58 else 'No'}")
    st.write(f"- TARGET_WALLET set: {'Yes' if TARGET_WALLET else 'No'}")
    st.write(f"- RPC_URL: {RPC_URL}")
    
    st.write(f"**Wallet Information:**")
    st.write(f"- Address: {str(wallet.pubkey())}")
    st.write(f"- Method used: {wallet_method}")
    
    if st.button("Test Connection"):
        try:
            result = client.get_health()
            st.success("RPC connection healthy!")
            
            balance = client.get_balance(wallet.pubkey())
            if balance.value is not None:
                st.success(f"Wallet accessible - Balance: {balance.value / 1e9:.6f} SOL")
            else:
                st.error("Could not fetch wallet balance")
        except Exception as e:
            st.error(f"Connection test failed: {e}")
