"""
Bounty Escrow Agent — LocalNet Deployment Script
Deploys the BountyEscrowAgent contract to AlgoKit LocalNet.

Usage (preferred — from repo root):
    algokit project deploy localnet

Direct:
    python smart_contracts/bounty_escrow/deploy_config.py

Prerequisites:
    1. Docker Desktop running
    2. `algokit localnet start` executed
    3. beaker-pyteal installed
"""

import json
import os
import sys

# Make emoji/status logging safe on Windows terminals.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add parent paths so we can import the contract
sys.path.insert(0, os.path.dirname(__file__))

from algosdk import account, mnemonic
from algosdk.v2client import algod
from algosdk import kmd
from beaker import client, sandbox

# ── LocalNet Connection ───────────────────────────────────────────────────────

ALGOD_ADDRESS = "http://localhost:4001"
ALGOD_TOKEN   = "a" * 64
KMD_ADDRESS   = "http://localhost:4002"
KMD_TOKEN     = "a" * 64

def get_localnet_accounts():
    """Get pre-funded accounts from AlgoKit LocalNet via KMD."""
    kmd_client = kmd.KMDClient(KMD_TOKEN, KMD_ADDRESS)

    # Find the default wallet
    wallets = kmd_client.list_wallets()
    default_wallet = None
    for w in wallets:
        if w["name"] == "unencrypted-default-wallet":
            default_wallet = w
            break

    if not default_wallet:
        raise RuntimeError("Default LocalNet wallet not found. Is LocalNet running?")

    wallet_handle = kmd_client.init_wallet_handle(default_wallet["id"], "")
    addresses = kmd_client.list_keys(wallet_handle)

    accounts = []
    for addr in addresses:
        private_key = kmd_client.export_key(wallet_handle, "", addr)
        accounts.append({
            "address": addr,
            "private_key": private_key,
            "mnemonic": mnemonic.from_private_key(private_key),
        })

    kmd_client.release_wallet_handle(wallet_handle)
    return accounts


def deploy():
    """Deploy BountyEscrowAgent to LocalNet."""

    print("=" * 60)
    print("🚀 Bounty Escrow Agent — LocalNet Deployment")
    print("=" * 60)

    # 1. Connect to LocalNet
    print("\n📡 Connecting to AlgoKit LocalNet...")
    algod_client = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)

    try:
        status = algod_client.status()
        print(f"   ✅ Connected! Last round: {status['last-round']}")
    except Exception as e:
        print(f"   ❌ Cannot connect to LocalNet: {e}")
        print("   💡 Run: algokit localnet start")
        sys.exit(1)

    # 2. Get pre-funded accounts
    print("\n🔑 Fetching LocalNet accounts from KMD...")
    accounts = get_localnet_accounts()

    if len(accounts) < 3:
        print(f"   ❌ Need at least 3 accounts, found {len(accounts)}")
        sys.exit(1)

    creator    = accounts[0]
    worker     = accounts[1]
    arbitrator = accounts[2]

    print(f"   👤 Creator:    {creator['address'][:20]}...")
    print(f"   👷 Worker:     {worker['address'][:20]}...")
    print(f"   ⚖️  Arbitrator: {arbitrator['address'][:20]}...")

    # Check balances
    for label, acct in [("Creator", creator), ("Worker", worker), ("Arbitrator", arbitrator)]:
        info = algod_client.account_info(acct["address"])
        balance_algo = info["amount"] / 1_000_000
        print(f"   💰 {label} balance: {balance_algo:.2f} ALGO")

    # 3. Import and build the contract
    print("\n🔨 Building smart contract...")
    from contract import app

    # 4. Deploy using Beaker ApplicationClient
    print("📜 Deploying to LocalNet...")

    from algosdk.atomic_transaction_composer import AccountTransactionSigner

    signer = AccountTransactionSigner(creator["private_key"])

    app_client = client.ApplicationClient(
        client=algod_client,
        app=app,
        signer=signer,
        sender=creator["address"],
    )

    try:
        app_id, app_addr, txid = app_client.create()
    except Exception as e:
        print(f"   ❌ Deployment failed: {e}")
        sys.exit(1)

    print(f"\n   ✅ Contract deployed!")
    print(f"   📋 App ID:      {app_id}")
    print(f"   💼 App Address:  {app_addr}")
    print(f"   🔄 TxID:        {txid}")

    # 5. Fund the contract address (minimum balance for inner txns)
    print("\n💸 Funding contract address (0.5 ALGO for MBR + inner txns + score boxes)...")
    from algosdk.transaction import PaymentTxn, wait_for_confirmation

    params = algod_client.suggested_params()
    fund_txn = PaymentTxn(
        sender=creator["address"],
        sp=params,
        receiver=app_addr,
        amt=500_000,  # 0.5 ALGO
    )
    signed_fund = fund_txn.sign(creator["private_key"])
    fund_txid = algod_client.send_transaction(signed_fund)
    wait_for_confirmation(algod_client, fund_txid, 4)
    print(f"   ✅ Funded! TxID: {fund_txid}")

    # 6. Verify deployment
    print("\n🔍 Verifying deployment...")
    app_info = algod_client.application_info(app_id)
    print(f"   ✅ Application exists on LocalNet")

    app_account = algod_client.account_info(app_addr)
    print(f"   ✅ Contract balance: {app_account['amount'] / 1_000_000:.4f} ALGO")

    # 7. Save deployment info
    deploy_info = {
        "app_id": app_id,
        "app_address": app_addr,
        "deploy_txid": txid,
        "network": "localnet",
        "algod_url": ALGOD_ADDRESS,
        "accounts": {
            "creator": creator["address"],
            "worker": worker["address"],
            "arbitrator": arbitrator["address"],
        },
    }

    info_path = os.path.join(os.path.dirname(__file__), "deploy_info.json")
    with open(info_path, "w") as f:
        json.dump(deploy_info, f, indent=2)
    print(f"\n📁 Deployment info saved to {info_path}")

    # 8. Summary
    print("\n" + "=" * 60)
    print("🎉 DEPLOYMENT COMPLETE!")
    print("=" * 60)
    print(f"\n   App ID:        {app_id}")
    print(f"   App Address:   {app_addr}")
    print(f"\n   Accounts:")
    print(f"   Creator:       {creator['address']}")
    print(f"   Worker:        {worker['address']}")
    print(f"   Arbitrator:    {arbitrator['address']}")
    print(f"\n   Explorer:      Run 'algokit explore' to open Lora")
    print(f"   Frontend:      Open frontend/index.html")
    print(f"   Timer:         5 minutes (anti-ghosting + dispute window)")
    print(f"\n   Next steps:")
    print(f"   1. python tests/test_bounty_escrow.py     # run E2E tests (from repo root)")
    print(f"   2. Open frontend/index.html               # interactive demo")
    print()

    return deploy_info


if __name__ == "__main__":
    deploy()
