# FIXED VERSION - Replace your wallet initialization section with this

def get_keypair_from_mnemonic_bip44(mnemonic: str, account_index: int = 0) -> Keypair:
    """Generate Solana keypair from mnemonic using BIP44 derivation - MOST COMMON"""
    try:
        clean_mnemonic = mnemonic.strip().strip('"\'')
        seed_bytes = Bip39SeedGenerator(clean_mnemonic).Generate()
        
        # BIP44 derivation path: m/44'/501'/account_index'/0'
        bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.SOLANA)
        bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(account_index)
        bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
        bip44_addr_ctx = bip44_chg_ctx.AddressIndex(0)
        
        private_key_bytes = bip44_addr_ctx.PrivateKey().Raw().ToBytes()[:32]
        return Keypair.from_bytes(private_key_bytes)
    except Exception as e:
        st.error(f"Error creating wallet from mnemonic (BIP44): {e}")
        raise

def get_keypair_from_mnemonic_simple(mnemonic: str) -> Keypair:
    """Generate Solana keypair from mnemonic using simple seed approach - ALTERNATIVE"""
    try:
        clean_mnemonic = mnemonic.strip().strip('"\'')
        seed_bytes = Bip39SeedGenerator(clean_mnemonic).Generate()
        private_key = seed_bytes[:32]
        return Keypair.from_seed(bytes(private_key))
    except Exception as e:
        st.error(f"Error creating wallet from mnemonic (simple): {e}")
        raise

def initialize_wallet_fixed():
    """Initialize wallet with the correct method - UPDATE THIS AFTER TESTING"""
    
    if not MNEMONIC:
        st.error("PRIVATE_KEY (mnemonic) environment variable not set!")
        st.stop()
    
    # REPLACE THIS SECTION WITH YOUR CORRECT METHOD AFTER TESTING
    # 
    # Option A: BIP44 Account 0 (most common - Phantom default)
    try:
        wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 0)
        method_used = "BIP44 Account 0 (Phantom/Solflare default)"
        st.info(f"Wallet loaded using {method_used}")
        return wallet, method_used
    except Exception as e:
        st.warning(f"BIP44 Account 0 failed: {e}")
    
    # Option B: Try BIP44 Account 1 (if Account 0 doesn't work)
    try:
        wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 1)
        method_used = "BIP44 Account 1"
        st.info(f"Wallet loaded using {method_used}")
        return wallet, method_used
    except Exception as e:
        st.warning(f"BIP44 Account 1 failed: {e}")
    
    # Option C: Simple seed method (last resort)
    try:
        wallet = get_keypair_from_mnemonic_simple(MNEMONIC)
        method_used = "Simple Seed Method"
        st.info(f"Wallet loaded using {method_used}")
        return wallet, method_used
    except Exception as e:
        st.error(f"Simple seed method failed: {e}")
    
    st.error("Could not initialize wallet with any method!")
    st.error("Please run the wallet finder tool first to identify the correct method")
    st.stop()

# SPECIFIC FIXES BASED ON COMMON SCENARIOS:

def initialize_wallet_phantom_default():
    """Use this if you're using Phantom wallet with default account"""
    wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 0)
    return wallet, "Phantom Default (BIP44 Account 0)"

def initialize_wallet_phantom_account_1():
    """Use this if you're using Phantom wallet with Account 1"""
    wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 1)
    return wallet, "Phantom Account 1 (BIP44 Account 1)"

def initialize_wallet_solflare_default():
    """Use this if you're using Solflare wallet with default account"""
    wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 0)
    return wallet, "Solflare Default (BIP44 Account 0)"

def initialize_wallet_trust_wallet():
    """Use this if you're using Trust Wallet or similar"""
    wallet = get_keypair_from_mnemonic_simple(MNEMONIC)
    return wallet, "Trust Wallet (Simple Seed)"

# STEP-BY-STEP INSTRUCTIONS:

"""
TO FIX YOUR WALLET ADDRESS ISSUE:

1. Run the wallet finder tool above first
2. Enter your 12-word mnemonic phrase
3. Enter your expected wallet address (from Phantom/Solflare)
4. The tool will tell you which method works

5. Then replace your initialize_wallet() function with ONE of these:

   # For Phantom/Solflare default account:
   def initialize_wallet():
       wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 0)
       return wallet, "BIP44 Account 0"

   # For Phantom/Solflare account 1:
   def initialize_wallet():
       wallet = get_keypair_from_mnemonic_bip44(MNEMONIC, 1)
       return wallet, "BIP44 Account 1"

   # For simple seed method:
   def initialize_wallet():
       wallet = get_keypair_from_mnemonic_simple(MNEMONIC)
       return wallet, "Simple Seed"

6. Update your .env file:
   PRIVATE_KEY="your 12 word mnemonic phrase here"
   # Remove or comment out PRIVATE_KEY_BASE58 if you're using mnemonic

COMMON WALLET DERIVATION PATHS:
- Phantom: BIP44 Account 0 (most common)
- Solflare: BIP44 Account 0 (most common)  
- Trust Wallet: Simple Seed method
- Exodus: BIP44 Account 0
- Ledger: BIP44 Account 0

If none of the standard methods work, your wallet might be using a custom derivation path.
"""
