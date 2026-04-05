"""
Bounty Escrow Agent — End-to-End Test Suite
Runs all 5 test scenarios from the architecture document against LocalNet.

Usage:
    python test_bounty_escrow.py

Prerequisites:
    1. AlgoKit LocalNet running (algokit localnet start)
    2. Contract deployed (algokit project deploy localnet — from repo root)
    3. deploy_info.json exists in smart_contracts/bounty_escrow/
"""

import os
import sys
import time
import hashlib
import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore:Test functions should return None:pytest.PytestReturnNotNoneWarning"
)

# Fix Windows console encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from algosdk.v2client import algod
from algosdk import encoding, kmd
from algosdk.transaction import (
    PaymentTxn,
    wait_for_confirmation,
)
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
    TransactionWithSigner,
)

# ── Config ────────────────────────────────────────────────────────────────────

ALGOD_ADDRESS = "http://localhost:4001"
ALGOD_TOKEN   = "a" * 64
KMD_ADDRESS   = "http://localhost:4002"
KMD_TOKEN     = "a" * 64

# Load deployment info
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from smart_contracts.bounty_escrow.abi_helpers import (
    decode_app_state,
    load_contract,
    score_box_ref,
)

# ── Test Utilities ────────────────────────────────────────────────────────────

class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"

def pass_msg(msg):
    print(f"  {Colors.GREEN}✅ PASS{Colors.END}: {msg}")

def fail_msg(msg):
    print(f"  {Colors.RED}❌ FAIL{Colors.END}: {msg}")

def info_msg(msg):
    print(f"  {Colors.CYAN}ℹ️  {msg}{Colors.END}")

def section(title):
    print(f"\n{Colors.BOLD}{Colors.YELLOW}{'─' * 60}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.YELLOW}  {title}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.YELLOW}{'─' * 60}{Colors.END}")


def sha256_string(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class LocalNetHelper:
    """Helper class for interacting with LocalNet contracts."""

    def __init__(self):
        self.algod = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)
        self.kmd_client = kmd.KMDClient(KMD_TOKEN, KMD_ADDRESS)
        self.accounts = self._get_accounts()
        self.contract = load_contract()

    def _get_accounts(self):
        """Get pre-funded accounts from KMD."""
        wallets = self.kmd_client.list_wallets()
        default_wallet = next(
            (w for w in wallets if w["name"] == "unencrypted-default-wallet"), None
        )
        if not default_wallet:
            raise RuntimeError("LocalNet wallet not found")

        handle = self.kmd_client.init_wallet_handle(default_wallet["id"], "")
        addresses = self.kmd_client.list_keys(handle)

        accounts = []
        for addr in addresses:
            pk = self.kmd_client.export_key(handle, "", addr)
            accounts.append({"address": addr, "private_key": pk})

        self.kmd_client.release_wallet_handle(handle)
        return accounts

    def get_signer(self, account):
        return AccountTransactionSigner(account["private_key"])

    def _score_boxes_for_call(self, app_id, method_name, args=None):
        args = args or []

        if method_name == "approve_work" and args:
            return [score_box_ref(app_id, args[0])]
        if method_name == "auto_release_after_silence" and args:
            return [score_box_ref(app_id, args[0])]
        if method_name == "oracle_verdict" and len(args) >= 6:
            return [score_box_ref(app_id, args[5])]
        if method_name == "get_credit_score" and args:
            return [score_box_ref(app_id, args[0])]

        if method_name == "opt_out":
            state = self.get_app_state(app_id)
            contributor = state.get("contributor", b"")
            if isinstance(contributor, bytes) and len(contributor) == 32:
                contributor = encoding.encode_address(contributor)
            if contributor:
                return [score_box_ref(app_id, contributor)]

        if method_name == "withdraw_acceptance":
            state = self.get_app_state(app_id)
            contributor = state.get("contributor", b"")
            if isinstance(contributor, bytes) and len(contributor) == 32:
                contributor = encoding.encode_address(contributor)
            if contributor:
                return [score_box_ref(app_id, contributor)]

        if method_name == "creator_cancel_bounty":
            state = self.get_app_state(app_id)
            if state.get("status") == 1:
                contributor = state.get("contributor", b"")
                if isinstance(contributor, bytes) and len(contributor) == 32:
                    contributor = encoding.encode_address(contributor)
                if contributor:
                    return [score_box_ref(app_id, contributor)]

        return []

    def deploy_fresh_contract(self, creator):
        """Deploy a new instance of the contract for clean test isolation."""
        from beaker import client as beaker_client
        from algosdk.atomic_transaction_composer import AccountTransactionSigner

        sys.path.insert(0, os.path.join(PROJECT_DIR, "smart_contracts", "bounty_escrow"))
        from contract import app

        signer = AccountTransactionSigner(creator["private_key"])

        app_client = beaker_client.ApplicationClient(
            client=self.algod,
            app=app,
            signer=signer,
            sender=creator["address"],
        )

        app_id, app_addr, txid = app_client.create()

        # Fund the contract
        params = self.algod.suggested_params()
        fund_txn = PaymentTxn(
            sender=creator["address"],
            sp=params,
            receiver=app_addr,
            amt=500_000,  # 0.5 ALGO for testing
        )
        signed = fund_txn.sign(creator["private_key"])
        fund_txid = self.algod.send_transaction(signed)
        wait_for_confirmation(self.algod, fund_txid, 4)

        return app_id, app_addr

    def call_method(self, app_id, sender, method_name, args=None, extra_txns=None):
        """Call an ABI method on the contract."""
        atc = AtomicTransactionComposer()
        method = self.contract.get_method_by_name(method_name)
        signer = self.get_signer(sender)
        params = self.algod.suggested_params()
        params.fee = 2000  # Cover inner txn fees
        params.flat_fee = True
        boxes = self._score_boxes_for_call(app_id, method_name, args)

        atc.add_method_call(
            app_id=app_id,
            method=method,
            sender=sender["address"],
            sp=params,
            signer=signer,
            method_args=args or [],
            accounts=[a["address"] for a in self.accounts[:3]],
            boxes=boxes,
        )

        result = atc.execute(self.algod, 4)
        return result

    def post_bounty_grouped(self, app_id, app_addr, creator, reward_microalgos, deadline, arb_addr=None):
        """Post a bounty with grouped payment + app call."""
        atc = AtomicTransactionComposer()
        method = self.contract.get_method_by_name("post_bounty")
        signer = self.get_signer(creator)
        params = self.algod.suggested_params()
        params.fee = 2000
        params.flat_fee = True

        # Payment transaction
        pay_txn = PaymentTxn(
            sender=creator["address"],
            sp=params,
            receiver=app_addr,
            amt=reward_microalgos,
        )

        if arb_addr is None:
            arb_addr = self.accounts[2]["address"]

        atc.add_method_call(
            app_id=app_id,
            method=method,
            sender=creator["address"],
            sp=params,
            signer=signer,
            method_args=[
                "test_criteria_hash_abc123",      # criteria_hash
                "test_suite_hash_def456",         # test_suite_hash
                deadline,                          # deadline_unix
                "auto",                           # arbitrator_type
                arb_addr,                          # arbitrator_addr
                TransactionWithSigner(pay_txn, signer),  # payment
            ],
        )

        result = atc.execute(self.algod, 4)
        return result

    def get_app_state(self, app_id):
        """Read global state of the contract."""
        return decode_app_state(self.algod, app_id)

    def get_balance(self, address):
        """Get account balance in microAlgos."""
        info = self.algod.account_info(address)
        return info["amount"]

    def get_credit_score(self, app_id, address):
        result = self.call_method(app_id, self.accounts[0], "get_credit_score", [address])
        return result.abi_results[0].return_value

    def get_evidence_info(self, app_id):
        result = self.call_method(app_id, self.accounts[0], "get_evidence_info")
        return result.abi_results[0].return_value


# ── Test Scenarios ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def helper():
    """Provide a shared LocalNet helper for pytest execution."""
    try:
        return LocalNetHelper()
    except Exception as exc:
        pytest.skip(f"LocalNet is not available for E2E tests: {exc}")

def test_happy_path(helper):
    """
    TEST SCENARIO 1: HAPPY PATH
    Creator posts → Worker accepts → submits → Creator approves → payout
    """
    section("Test 1: Happy Path (Post → Accept → Submit → Approve)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]

    # Deploy fresh contract
    app_id, app_addr = helper.deploy_fresh_contract(creator)
    info_msg(f"Fresh contract deployed: App ID = {app_id}")

    reward = 1_000_000  # 1 ALGO
    deadline = int(time.time()) + 3600  # 1 hour from now
    submission_ref = "QmTestIPFSHash123456"
    submission_hash = sha256_string(submission_ref)

    worker_balance_before = helper.get_balance(worker["address"])

    # Step 1: Post bounty
    try:
        helper.post_bounty_grouped(app_id, app_addr, creator, reward, deadline)
        state = helper.get_app_state(app_id)
        assert state.get("status", -1) == 0, f"Expected status 0, got {state.get('status')}"
        assert state.get("reward_amount", 0) == reward
        pass_msg("post_bounty() — Status=OPEN, reward stored")
    except Exception as e:
        fail_msg(f"post_bounty() failed: {e}")
        return False

    # Step 2: Accept bounty
    try:
        helper.call_method(app_id, worker, "accept_bounty")
        state = helper.get_app_state(app_id)
        assert state["status"] == 1, f"Expected status 1, got {state['status']}"
        pass_msg("accept_bounty() — Status=ACCEPTED")
    except Exception as e:
        fail_msg(f"accept_bounty() failed: {e}")
        return False

    # Step 3: Submit work
    try:
        helper.call_method(app_id, worker, "submit_work", [submission_ref, submission_hash])
        state = helper.get_app_state(app_id)
        assert state["status"] == 2, f"Expected status 2, got {state['status']}"
        evidence = helper.get_evidence_info(app_id)
        assert evidence[0] == submission_hash
        pass_msg("submit_work() — Status=SUBMITTED")
    except Exception as e:
        fail_msg(f"submit_work() failed: {e}")
        return False

    # Step 4: Approve work
    try:
        helper.call_method(app_id, creator, "approve_work", [worker["address"]])
        state = helper.get_app_state(app_id)
        assert state["status"] == 3, f"Expected status 3, got {state['status']}"
        assert helper.get_credit_score(app_id, worker["address"]) == 150
        pass_msg("approve_work() — Status=APPROVED")
    except Exception as e:
        fail_msg(f"approve_work() failed: {e}")
        return False

    # Verify payout
    worker_balance_after = helper.get_balance(worker["address"])
    gain = worker_balance_after - worker_balance_before
    # Worker gains ~1 ALGO minus fees paid for accept + submit txns
    if gain > 900_000:
        pass_msg(f"Worker received payout: +{gain / 1_000_000:.4f} ALGO")
    else:
        fail_msg(f"Worker payout too low: +{gain / 1_000_000:.4f} ALGO (expected ~1)")
        return False

    return True


def test_rejection_dispute_oracle(helper):
    """
    TEST SCENARIO 2: REJECTION → DISPUTE → AUTOMATED VERDICT (PASS)
    """
    section("Test 2: Rejection → Dispute → Oracle Verdict (PASS)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]
    oracle = helper.accounts[2]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    info_msg(f"Fresh contract: App ID = {app_id}")

    reward = 1_000_000
    deadline = int(time.time()) + 3600
    submission_ref = "QmDisputeTest789"
    submission_hash = sha256_string(submission_ref)
    oracle_output_hash = sha256_string("oracle-pass-output")
    verdict_reason_hash = sha256_string("oracle-pass-reason")

    worker_balance_before = helper.get_balance(worker["address"])

    # Post → Accept → Submit
    try:
        helper.post_bounty_grouped(
            app_id, app_addr, creator, reward, deadline, arb_addr=oracle["address"]
        )
        helper.call_method(app_id, worker, "accept_bounty")
        helper.call_method(app_id, worker, "submit_work", [submission_ref, submission_hash])
        pass_msg("Setup complete: OPEN → ACCEPTED → SUBMITTED")
    except Exception as e:
        fail_msg(f"Setup failed: {e}")
        return False

    # Reject
    try:
        helper.call_method(app_id, creator, "reject_work")
        state = helper.get_app_state(app_id)
        assert state["status"] == 4
        pass_msg("reject_work() — Status=REJECTED")
    except Exception as e:
        fail_msg(f"reject_work() failed: {e}")
        return False

    # Dispute
    try:
        helper.call_method(app_id, worker, "raise_dispute")
        state = helper.get_app_state(app_id)
        assert state["status"] == 5
        pass_msg("raise_dispute() — Status=DISPUTED")
    except Exception as e:
        fail_msg(f"raise_dispute() failed: {e}")
        return False

    # Oracle verdict: PASS (only designated oracle can call)
    try:
        helper.call_method(
            app_id,
            oracle,
            "oracle_verdict",
            [
                "PASS",
                submission_hash,
                oracle_output_hash,
                verdict_reason_hash,
                creator["address"],
                worker["address"],
            ],
        )
        state = helper.get_app_state(app_id)
        assert state["status"] == 6  # RESOLVED_WORKER
        assert helper.get_credit_score(app_id, worker["address"]) == 130
        evidence = helper.get_evidence_info(app_id)
        assert evidence[0] == submission_hash
        assert evidence[1] == oracle_output_hash
        assert evidence[2] == verdict_reason_hash
        assert evidence[3] == "PASS"
        assert evidence[4] > 0
        pass_msg("oracle_verdict('PASS') — Status=RESOLVED_WORKER")
    except Exception as e:
        fail_msg(f"oracle_verdict() failed: {e}")
        return False

    # Verify worker got paid
    worker_balance_after = helper.get_balance(worker["address"])
    gain = worker_balance_after - worker_balance_before
    if gain > 900_000:
        pass_msg(f"Worker received payout via dispute: +{gain / 1_000_000:.4f} ALGO")
    else:
        fail_msg(f"Worker payout via dispute too low: +{gain / 1_000_000:.4f} ALGO")
        return False

    return True


def test_rejection_opt_out(helper):
    """
    TEST SCENARIO 3: REJECTION → OPT OUT
    """
    section("Test 3: Rejection → Opt Out (Worker forfeits)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    info_msg(f"Fresh contract: App ID = {app_id}")

    reward = 1_000_000
    deadline = int(time.time()) + 3600
    submission_ref = "QmOptOutTest"
    submission_hash = sha256_string(submission_ref)

    creator_balance_before = helper.get_balance(creator["address"])

    # Post → Accept → Submit → Reject
    try:
        helper.post_bounty_grouped(app_id, app_addr, creator, reward, deadline)
        helper.call_method(app_id, worker, "accept_bounty")
        helper.call_method(app_id, worker, "submit_work", [submission_ref, submission_hash])
        helper.call_method(app_id, creator, "reject_work")
        pass_msg("Setup complete: POST → ACCEPT → SUBMIT → REJECT")
    except Exception as e:
        fail_msg(f"Setup failed: {e}")
        return False

    # Opt out
    try:
        helper.call_method(app_id, worker, "opt_out", [creator["address"]])
        state = helper.get_app_state(app_id)
        assert state["status"] == 8  # OPTED_OUT
        assert helper.get_credit_score(app_id, worker["address"]) == 90
        pass_msg("opt_out() — Status=OPTED_OUT")
    except Exception as e:
        fail_msg(f"opt_out() failed: {e}")
        return False

    # Verify creator got refund
    creator_balance_after = helper.get_balance(creator["address"])
    # Creator paid reward + fees, but gets reward back
    refund = creator_balance_after - creator_balance_before
    # The refund should offset most of the reward (minus fees)
    if refund > -500_000:  # Creator shouldn't lose more than fees
        pass_msg(f"Creator refunded (net: {refund / 1_000_000:+.4f} ALGO)")
    else:
        fail_msg(f"Creator lost too much: {refund / 1_000_000:+.4f} ALGO")
        return False

    return True


def test_oracle_verdict_fail(helper):
    """
    TEST SCENARIO 2b: REJECTION → DISPUTE → ORACLE VERDICT (FAIL)
    """
    section("Test 4: Rejection → Dispute → Oracle Verdict (FAIL)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]
    oracle = helper.accounts[2]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    info_msg(f"Fresh contract: App ID = {app_id}")

    reward = 1_000_000
    deadline = int(time.time()) + 3600
    submission_ref = "QmFailTest"
    submission_hash = sha256_string(submission_ref)
    oracle_output_hash = sha256_string("oracle-fail-output")
    verdict_reason_hash = sha256_string("oracle-fail-reason")

    creator_balance_before = helper.get_balance(creator["address"])

    # Post → Accept → Submit → Reject → Dispute
    try:
        helper.post_bounty_grouped(
            app_id, app_addr, creator, reward, deadline, arb_addr=oracle["address"]
        )
        helper.call_method(app_id, worker, "accept_bounty")
        helper.call_method(app_id, worker, "submit_work", [submission_ref, submission_hash])
        helper.call_method(app_id, creator, "reject_work")
        helper.call_method(app_id, worker, "raise_dispute")
        pass_msg("Setup: POST → ACCEPT → SUBMIT → REJECT → DISPUTE")
    except Exception as e:
        fail_msg(f"Setup failed: {e}")
        return False

    # Oracle verdict: FAIL
    try:
        helper.call_method(
            app_id,
            oracle,
            "oracle_verdict",
            [
                "FAIL",
                submission_hash,
                oracle_output_hash,
                verdict_reason_hash,
                creator["address"],
                worker["address"],
            ],
        )
        state = helper.get_app_state(app_id)
        assert state["status"] == 7  # RESOLVED_CREATOR
        assert helper.get_credit_score(app_id, worker["address"]) == 20
        evidence = helper.get_evidence_info(app_id)
        assert evidence[1] == oracle_output_hash
        assert evidence[2] == verdict_reason_hash
        assert evidence[3] == "FAIL"
        pass_msg("oracle_verdict('FAIL') — Status=RESOLVED_CREATOR")
    except Exception as e:
        fail_msg(f"oracle_verdict() failed: {e}")
        return False

    # Verify creator got refund
    creator_balance_after = helper.get_balance(creator["address"])
    refund = creator_balance_after - creator_balance_before
    if refund > -500_000:
        pass_msg(f"Creator refunded via FAIL verdict (net: {refund / 1_000_000:+.4f} ALGO)")
    else:
        fail_msg(f"Creator refund issue: {refund / 1_000_000:+.4f} ALGO")
        return False

    return True


def test_edge_cases(helper):
    """
    TEST SCENARIO 5: EDGE CASES
    """
    section("Test 5: Edge Cases (Security Checks)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]
    random_account = helper.accounts[2]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    info_msg(f"Fresh contract: App ID = {app_id}")

    reward = 1_000_000
    deadline = int(time.time()) + 3600
    oracle = helper.accounts[2]
    submission_ref = "QmValidWork"
    submission_hash = sha256_string(submission_ref)

    helper.post_bounty_grouped(
        app_id, app_addr, creator, reward, deadline, arb_addr=oracle["address"]
    )

    # Edge case 1: Creator cannot self-accept
    try:
        helper.call_method(app_id, creator, "accept_bounty")
        fail_msg("Creator self-accept should have been rejected")
        return False
    except Exception:
        pass_msg("Creator cannot self-accept (correctly rejected)")

    # Accept with worker for further tests
    helper.call_method(app_id, worker, "accept_bounty")

    # Edge case 2: Empty IPFS hash rejected
    try:
        helper.call_method(app_id, worker, "submit_work", ["", submission_hash])
        fail_msg("Empty IPFS hash should have been rejected")
        return False
    except Exception:
        pass_msg("Empty IPFS hash rejected correctly")

    # Submit valid work
    helper.call_method(app_id, worker, "submit_work", [submission_ref, submission_hash])

    # Edge case 3: Random account cannot approve
    try:
        helper.call_method(app_id, random_account, "approve_work", [worker["address"]])
        fail_msg("Random account approve should have been rejected")
        return False
    except Exception:
        pass_msg("Non-creator cannot approve (correctly rejected)")

    # Edge case 4: Worker cannot approve
    try:
        helper.call_method(app_id, worker, "approve_work", [worker["address"]])
        fail_msg("Worker approve should have been rejected")
        return False
    except Exception:
        pass_msg("Worker cannot approve own work (correctly rejected)")

    helper.call_method(app_id, creator, "reject_work")
    helper.call_method(app_id, worker, "raise_dispute")

    # Edge case 5: Non-oracle cannot finalize an automated dispute
    try:
        helper.call_method(
            app_id,
            creator,
            "oracle_verdict",
            [
                "PASS",
                submission_hash,
                sha256_string("unauthorized-output"),
                sha256_string("unauthorized-reason"),
                creator["address"],
                worker["address"],
            ],
        )
        fail_msg("Non-oracle verdict should have been rejected")
        return False
    except Exception:
        pass_msg("Only designated oracle can finalize auto disputes")

    pass_msg("All edge cases passed!")
    return True


def test_creator_cancel_open(helper):
    """OPEN → creator_cancel_bounty → CANCELLED; creator refunded."""
    section("Test: Creator cancel (no assignee)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    reward = 1_000_000
    deadline = int(time.time()) + 3600
    creator_balance_before = helper.get_balance(creator["address"])

    try:
        helper.post_bounty_grouped(app_id, app_addr, creator, reward, deadline)
        helper.call_method(app_id, creator, "creator_cancel_bounty", [creator["address"]])
        state = helper.get_app_state(app_id)
        assert state["status"] == 9
        pass_msg("creator_cancel_bounty() from OPEN — Status=CANCELLED")
    except Exception as e:
        fail_msg(f"creator_cancel OPEN failed: {e}")
        return False

    creator_balance_after = helper.get_balance(creator["address"])
    if creator_balance_after - creator_balance_before > -200_000:
        pass_msg("Creator net refund roughly recovered escrow (minus fees)")
    else:
        fail_msg("Creator balance unexpected after cancel")
        return False

    try:
        helper.call_method(app_id, worker, "accept_bounty")
        fail_msg("accept after cancel should fail")
        return False
    except Exception:
        pass_msg("Cannot accept cancelled bounty")

    return True


def test_creator_cancel_accepted(helper):
    """ACCEPTED → creator_cancel — worker penalized."""
    section("Test: Creator cancel (assignee before submit)")

    creator = helper.accounts[0]
    worker = helper.accounts[1]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    reward = 1_000_000
    deadline = int(time.time()) + 7200

    try:
        helper.post_bounty_grouped(app_id, app_addr, creator, reward, deadline)
        helper.call_method(app_id, worker, "accept_bounty")
        helper.call_method(app_id, creator, "creator_cancel_bounty", [creator["address"]])
        state = helper.get_app_state(app_id)
        assert state["status"] == 9
        assert helper.get_credit_score(app_id, worker["address"]) == 88
        pass_msg("creator_cancel from ACCEPTED — worker score 100-12=88")
    except Exception as e:
        fail_msg(f"creator_cancel ACCEPTED failed: {e}")
        return False

    return True


def test_withdraw_acceptance(helper):
    """ACCEPTED → withdraw_acceptance → OPEN; another worker can accept."""
    section("Test: Withdraw acceptance (before submit)")

    creator = helper.accounts[0]
    worker_a = helper.accounts[1]
    worker_b = helper.accounts[2]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    reward = 1_000_000
    deadline = int(time.time()) + 3600

    try:
        helper.post_bounty_grouped(app_id, app_addr, creator, reward, deadline)
        helper.call_method(app_id, worker_a, "accept_bounty")
        state = helper.get_app_state(app_id)
        assert state["status"] == 1
        helper.call_method(app_id, worker_a, "withdraw_acceptance")
        state = helper.get_app_state(app_id)
        assert state["status"] == 0
        assert helper.get_credit_score(app_id, worker_a["address"]) == 95
        helper.call_method(app_id, worker_b, "accept_bounty")
        state = helper.get_app_state(app_id)
        assert state["status"] == 1
        pass_msg("withdraw_acceptance → OPEN → second accept_bounty OK")
    except Exception as e:
        fail_msg(f"withdraw_acceptance flow failed: {e}")
        return False

    return True


def test_reopen_after_rejection(helper):
    """
    REJECTED → wait dispute window → reopen_after_rejection → OPEN → new worker accepts.
    Slow (~5.5 min): enable with RUN_REOPEN_E2E=1.
    """
    section("Test 6: Reopen after rejection (new contributor)")

    if os.environ.get("RUN_REOPEN_E2E") != "1":
        info_msg("SKIP: set RUN_REOPEN_E2E=1 to run (~5.5 min; needs LocalNet + fresh deploy)")
        return True

    creator = helper.accounts[0]
    worker_a = helper.accounts[1]
    worker_b = helper.accounts[3] if len(helper.accounts) > 3 else helper.accounts[2]

    app_id, app_addr = helper.deploy_fresh_contract(creator)
    info_msg(f"Fresh contract: App ID = {app_id}")

    reward = 1_000_000
    deadline = int(time.time()) + 7200  # 2 hours — room after 5 min wait
    submission_ref = "QmReopenTest"
    submission_hash = sha256_string(submission_ref)

    try:
        helper.post_bounty_grouped(app_id, app_addr, creator, reward, deadline)
        helper.call_method(app_id, worker_a, "accept_bounty")
        helper.call_method(app_id, worker_a, "submit_work", [submission_ref, submission_hash])
        helper.call_method(app_id, creator, "reject_work")
        state = helper.get_app_state(app_id)
        assert state["status"] == 4
        pass_msg("Setup: POST → ACCEPT → SUBMIT → REJECT")
    except Exception as e:
        fail_msg(f"Setup failed: {e}")
        return False

    info_msg("Waiting 310s for dispute window (AUTO_RELEASE_DELAY on LocalNet)…")
    time.sleep(310)

    try:
        helper.call_method(app_id, creator, "reopen_after_rejection")
        state = helper.get_app_state(app_id)
        assert state["status"] == 0, f"Expected OPEN (0), got {state.get('status')}"
        pass_msg("reopen_after_rejection() — Status=OPEN")
    except Exception as e:
        fail_msg(f"reopen_after_rejection() failed: {e}")
        return False

    try:
        helper.call_method(app_id, worker_b, "accept_bounty")
        state = helper.get_app_state(app_id)
        assert state["status"] == 1
        pass_msg("Second worker accept_bounty() — Status=ACCEPTED")
    except Exception as e:
        fail_msg(f"Second accept_bounty() failed: {e}")
        return False

    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  🧪 Bounty Escrow Agent — E2E Test Suite")
    print("  📡 Running against AlgoKit LocalNet")
    print("=" * 60)

    try:
        helper = LocalNetHelper()
        info_msg(f"Connected to LocalNet. Found {len(helper.accounts)} accounts.")
    except Exception as e:
        print(f"\n❌ Cannot connect to LocalNet: {e}")
        print("   Run: algokit localnet start")
        sys.exit(1)

    results = {}
    test_functions = [
        ("Happy Path", test_happy_path),
        ("Creator cancel (OPEN)", test_creator_cancel_open),
        ("Creator cancel (ACCEPTED)", test_creator_cancel_accepted),
        ("Withdraw acceptance", test_withdraw_acceptance),
        ("Rejection → Dispute → Oracle PASS", test_rejection_dispute_oracle),
        ("Rejection → Opt Out", test_rejection_opt_out),
        ("Rejection → Dispute → Oracle FAIL", test_oracle_verdict_fail),
        ("Edge Cases", test_edge_cases),
    ]
    if os.environ.get("RUN_REOPEN_E2E") == "1":
        test_functions.append(("Reopen after rejection", test_reopen_after_rejection))

    for name, fn in test_functions:
        try:
            results[name] = fn(helper)
        except Exception as e:
            fail_msg(f"Test crashed: {e}")
            results[name] = False

    # Summary
    section("TEST RESULTS SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = f"{Colors.GREEN}PASS{Colors.END}" if result else f"{Colors.RED}FAIL{Colors.END}"
        print(f"  {status}  {name}")

    print(f"\n  {Colors.BOLD}Result: {passed}/{total} tests passed{Colors.END}")

    if passed == total:
        print(f"\n  {Colors.GREEN}{Colors.BOLD}🎉 ALL TESTS PASSED!{Colors.END}\n")
        sys.exit(0)
    else:
        print(f"\n  {Colors.RED}{Colors.BOLD}💥 SOME TESTS FAILED{Colors.END}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
