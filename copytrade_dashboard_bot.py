import os
import time
import json
import streamlit as st
import requests
from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.publickey import PublicKey
from solana.transaction import Transaction
from solana.rpc.types import TxOpts
from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins
from dotenv import load_dotenv
from typing import Dict, List, Optional
import asyncio

# Load environment variables
load_dotenv()

# Configuration
MNEMONIC = os.getenv("PRIVATE_KEY")
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

# Wallet setup
def get_keypair_from_mnemonic(mnemonic: str) -> Keypair:
    seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
    bip44 = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
    priv_key = bip44.PrivateKey().Raw().ToBytes()
    return Keypair.from_secret_key(priv_key)

wallet = get_keypair_from_mnemonic(MNEMONIC)
client = Client(RPC_URL)

# Jupiter API functions
def get_token_price(token_address: str) -> Optional[float]:
    """Get current token price in USD"""
    try:
        response = requests.get(f"{JUPITER_API_URL}/price?ids={token_address}")
        if response.status_code == 200:
            data = response.json()
            return data.get('data', {}).get(token_address, {}).get('price', 0)
    except Exception as e:
        st.error(f"Error fetching price: {e}")
    return None

def get_jupiter_quote(input_mint: str, output_mint: str, amount: int):
    """Get swap quote from Jupiter"""
    try:
        params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': amount,
            'slippageBps': 50  # 0.5% slippage
        }
        response = requests.get(f"{JUPITER_API_URL}/quote", params=params)
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        st.error(f"Error getting quote: {e}")
        return None

def execute_jupiter_swap(quote_response: dict) -> bool:
    """Execute swap using Jupiter API"""
    try:
        # Get swap transaction
        swap_payload = {
            'quoteResponse': quote_response,
            'userPublicKey': str(wallet.public_key),
            'wrapAndUnwrapSol': True
        }
        
        response = requests.post(f"{JUPITER_API_URL}/swap", json=swap_payload)
        if response.status_code != 200:
            return False
            
        swap_data = response.json()
        
        # Deserialize and sign transaction
        transaction = Transaction.deserialize(swap_data['swapTransaction'])
        transaction.sign(wallet)
        
        # Send transaction
        result = client.send_transaction(transaction)
        
        if result.value:
            st.success(f"Swap executed: {result.value}")
            return True
        else:
            st.error("Transaction failed")
            return False
            
    except Exception as e:
        st.error(f"Error executing swap: {e}")
        return False

def parse_transaction_for_tokens(tx_signature: str) -> List[Dict]:
    """Parse transaction to extract token swap information"""
    try:
        tx_data = client.get_transaction(tx_signature)
        
        # This is a simplified parser - you'd need more sophisticated parsing
        # to extract exact token addresses and amounts from the transaction
        
        tokens = []
        # TODO: Implement proper transaction parsing
        # This would involve analyzing the transaction instructions
        # to identify token swaps and extract:
        # - Input token address
        # - Output token address  
        # - Amount
        
        return tokens
    except Exception as e:
        st.error(f"Error parsing transaction: {e}")
        return []

def copy_trade(tx_signature: str) -> bool:
    """Copy a trade from target wallet with fixed 0.03 SOL buy amount"""
    try:
        # Parse the original transaction to get the token being bought
        tokens = parse_transaction_for_tokens(tx_signature)
        
        if not tokens:
            st.warning("Could not parse tokens from transaction")
            return False
            
        for token_info in tokens:
            # We only care about the output token (what they're buying)
            output_token = token_info['output_token']
            
            # Skip if they're buying SOL (we want to buy tokens with SOL)
            if output_token == SOL_MINT:
                continue
            
            st.info(f"Copying trade: Buying {output_token[:8]}... with 0.03 SOL")
            
            # Get quote to buy the same token with 0.03 SOL
            quote = get_jupiter_quote(
                input_mint=SOL_MINT,  # We're spending SOL
                output_mint=output_token,  # We're buying this token
                amount=BUY_AMOUNT_LAMPORTS  # 0.03 SOL in lamports
            )
            
            if not quote:
                st.error("Could not get quote for trade")
                continue
                
            # Execute the swap
            if execute_jupiter_swap(quote):
                # Track the position
                entry_price = get_token_price(output_token)
                tokens_received = int(quote['outAmount'])
                
                st.session_state.positions[output_token] = {
                    'amount': tokens_received,
                    'entry_price': entry_price,
                    'timestamp': time.time(),
                    'sol_spent': BUY_AMOUNT_SOL
                }
                
                st.success(f"Bought {tokens_received:,} tokens for 0.03 SOL")
                return True
            else:
                st.error("Failed to execute swap")
        
        return False
        
    except Exception as e:
        st.error(f"Error copying trade: {e}")
        return False

def check_and_sell_on_profit():
    """Check positions and sell 50% at 100% profit"""
    positions_to_remove = []
    
    for token_address, position in st.session_state.positions.items():
        current_price = get_token_price(token_address)
        
        if current_price and position['entry_price']:
            profit_percentage = ((current_price - position['entry_price']) / position['entry_price']) * 100
            
            if profit_percentage >= PROFIT_TARGET:  # 100% profit - sell 50%
                # Sell 50% of position
                sell_amount = position['amount'] // 2
                
                st.info(f"Taking 50% profit on {token_address[:8]}... at {profit_percentage:.1f}% gain")
                
                # Get quote to sell 50% back to SOL
                quote = get_jupiter_quote(
                    input_mint=token_address,
                    output_mint=SOL_MINT,  # Sell back to SOL
                    amount=sell_amount
                )
                
                if quote and execute_jupiter_swap(quote):
                    # Calculate SOL received
                    sol_received = int(quote['outAmount']) / 1_000_000_000
                    
                    # Update position
                    st.session_state.positions[token_address]['amount'] -= sell_amount
                    
                    st.success(f"Sold 50% for {sol_received:.4f} SOL at {profit_percentage:.1f}% profit!")
                    
                    # Remove position if amount is too small
                    if st.session_state.positions[token_address]['amount'] <= 100:
                        positions_to_remove.append(token_address)
                        st.info(f"Position closed for {token_address[:8]}...")
                else:
                    st.error("Failed to execute profit-taking sell")
    
    # Remove closed positions
    for token_address in positions_to_remove:
        del st.session_state.positions[token_address]

def check_and_execute_stop_loss():
    """Check positions and execute stop-loss at -50%"""
    positions_to_remove = []
    
    for token_address, position in st.session_state.positions.items():
        current_price = get_token_price(token_address)
        
        if current_price and position['entry_price']:
            profit_percentage = ((current_price - position['entry_price']) / position['entry_price']) * 100
            
            if profit_percentage <= STOP_LOSS_PERCENTAGE:  # -50% loss - sell everything
                st.warning(f"STOP-LOSS TRIGGERED for {token_address[:8]}... at {profit_percentage:.1f}% loss!")
                
                # Sell entire position
                sell_amount = position['amount']
                
                # Get quote to sell everything back to SOL
                quote = get_jupiter_quote(
                    input_mint=token_address,
                    output_mint=SOL_MINT,
                    amount=sell_amount
                )
                
                if quote and execute_jupiter_swap(quote):
                    # Calculate SOL received
                    sol_received = int(quote['outAmount']) / 1_000_000_000
                    sol_spent = position.get('sol_spent', BUY_AMOUNT_SOL)
                    loss_amount = sol_spent - sol_received
                    
                    st.error(f"STOP-LOSS: Sold all for {sol_received:.4f} SOL (Loss: {loss_amount:.4f} SOL)")
                    
                    # Mark position for removal
                    positions_to_remove.append(token_address)
                else:
                    st.error(f"Failed to execute stop-loss for {token_address[:8]}...")
    
    # Remove stopped-out positions
    for token_address in positions_to_remove:
        del st.session_state.positions[token_address]

# Streamlit UI
st.title("Solana Copy Trading Bot - LIVE TRADING")
st.warning(WARNING: This bot will execute real trades with real money!")

# Display configuration
st.info(f"**Buy Amount:** {BUY_AMOUNT_SOL} SOL per trade")
st.info(f" **Profit Target:** {PROFIT_TARGET}% (sell 50% of position)")
st.error(f" **Stop-Loss:** {STOP_LOSS_PERCENTAGE}% (sell entire position)")

# Display wallet info
st.write(f"**Wallet:** {wallet.public_key}")
st.write(f"**Target Wallet:** {TARGET_WALLET}")

# Check SOL balance
try:
    balance = client.get_balance(wallet.public_key)
    sol_balance = balance.value / 1_000_000_000
    st.write(f"**SOL Balance:** {sol_balance:.4f} SOL")
    
    if sol_balance < BUY_AMOUNT_SOL:
        st.error(f"Insufficient SOL balance! Need at least {BUY_AMOUNT_SOL} SOL to trade.")
except Exception as e:
    st.error(f"Error checking balance: {e}")

# Control panel
col1, col2 = st.columns(2)

with col1:
    st.subheader("Bot Status")
    if st.button("START TRADING", type="primary"):
        st.session_state.trading_active = True
        st.success("Trading bot activated!")
    
    if st.button("STOP TRADING", type="secondary"):
        st.session_state.trading_active = False
        st.error("Trading bot stopped!")

with col2:
    st.subheader("Current Positions")
    if st.session_state.positions:
        for token, position in st.session_state.positions.items():
            current_price = get_token_price(token)
            if current_price and position['entry_price']:
                profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
                sol_spent = position.get('sol_spent', 0)
                
                # Color code based on profit
                if profit_pct >= PROFIT_TARGET:
                    st.success(f"**{token[:8]}...**: +{profit_pct:.1f}% (Ready to sell 50%!)")
                elif profit_pct > 0:
                    st.info(f" **{token[:8]}...**: +{profit_pct:.1f}% ({sol_spent} SOL)")
                elif profit_pct <= STOP_LOSS_PERCENTAGE:
                    st.error(f" **{token[:8]}...**: {profit_pct:.1f}% (STOP-LOSS TRIGGERED!)")
                else:
                    st.warning(f"**{token[:8]}...**: {profit_pct:.1f}% ({sol_spent} SOL)")
            else:
                st.write(f"**{token[:8]}...**: Price data unavailable")
    else:
        st.write("No open positions")

# Main trading loop
if st.session_state.get('trading_active', False):
    st.info("Bot is actively monitoring and trading...")
    
    # Fetch new transactions
    try:
        result = client.get_signatures_for_address(PublicKey(TARGET_WALLET), limit=5)
        transactions = result.value if hasattr(result, 'value') else []
        
        # Process new transactions
        for tx in transactions:
            sig = tx.get('signature', '')
            if sig and sig not in st.session_state.executed_trades:
                st.write(f"Processing new transaction: {sig[:8]}...")
                
                if copy_trade(sig):
                    st.session_state.executed_trades.add(sig)
                    st.success(f"Successfully copied trade: {sig[:8]}...")
                else:
                    st.warning(f"Could not copy trade: {sig[:8]}...")
        
        # Check for profit-taking opportunities
        check_and_sell_on_profit()
        
        # Check for stop-loss triggers
        check_and_execute_stop_loss()
        
    except Exception as e:
        st.error(f"Error in trading loop: {e}")

else:
    st.info("Bot is stopped. Click 'START TRADING' to begin.")

# Display recent activity
st.subheader("Recent Activity")

# Show P&L summary
if st.session_state.positions:
    total_positions = len(st.session_state.positions)
    total_invested = sum(pos.get('sol_spent', BUY_AMOUNT_SOL) for pos in st.session_state.positions.values())
    
    st.write(f"**Active Positions:** {total_positions}")
    st.write(f"**Total Invested:** {total_invested:.4f} SOL")

if st.session_state.executed_trades:
    st.write(f"**Total Trades Executed:** {len(st.session_state.executed_trades)}")
    for sig in list(st.session_state.executed_trades)[-5:]:
        st.code(sig)
else:
    st.write("No trades executed yet.")

# Auto-refresh
if st.session_state.get('trading_active', False):
    time.sleep(5)
    st.rerun()