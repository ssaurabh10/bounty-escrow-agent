"""
Bounty Escrow Agent — Smart Contract (Algorand Python / algopy)
Deployed on AlgoKit LocalNet

State Machine:
  OPEN(0) → ACCEPTED(1) → SUBMITTED(2) → APPROVED(3) | REJECTED(4)
  REJECTED → DISPUTED(5) | OPTED_OUT(8)
  DISPUTED → RESOLVED_WORKER(6) | RESOLVED_CREATOR(7)

Team Marcos.dev | BIT Sindri | Hackatron 3.0
"""

from beaker import Application
from beaker.lib.storage import BoxMapping
from pyteal import (
    Seq, Assert, If, Int, Bytes, Expr, Len, Txn, Global,
    InnerTxnBuilder, TxnField, TxnType, Subroutine, TealType,
    abi, Not, Or, ScratchVar,
)


# ── Status Constants ──────────────────────────────────────────────────────────
STATUS_OPEN             = Int(0)
STATUS_ACCEPTED         = Int(1)
STATUS_SUBMITTED        = Int(2)
STATUS_APPROVED         = Int(3)
STATUS_REJECTED         = Int(4)
STATUS_DISPUTED         = Int(5)
STATUS_RESOLVED_WORKER  = Int(6)
STATUS_RESOLVED_CREATOR = Int(7)
STATUS_OPTED_OUT        = Int(8)

# ── Timer: 5 minutes for LocalNet demo (300 seconds) ─────────────────────────
# In production this would be 5 * 24 * 60 * 60 = 432000 (5 days)
AUTO_RELEASE_DELAY = Int(300)  # 5 minutes for LocalNet testing

# Reputation score constants
SCORE_START = Int(100)
SCORE_MAX = Int(1000)
SCORE_APPROVED = Int(50)
SCORE_AUTO_RELEASE = Int(20)
SCORE_DISPUTE_WIN = Int(30)
SCORE_DISPUTE_LOSS = Int(80)
SCORE_OPT_OUT = Int(10)
HASH_HEX_LEN = Int(64)


# ── Global State Schema ──────────────────────────────────────────────────────

class BountyState:
    from beaker import GlobalStateValue

    creator          = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    contributor      = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    reward_amount    = GlobalStateValue(TealType.uint64, default=Int(0))
    deadline         = GlobalStateValue(TealType.uint64, default=Int(0))
    criteria_hash    = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    test_suite_hash  = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    work_ipfs_hash   = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    submission_hash  = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    arbitrator_type  = GlobalStateValue(TealType.bytes,  default=Bytes("auto"))
    arbitrator_addr  = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    verdict_code     = GlobalStateValue(TealType.bytes,  default=Bytes(""))
    oracle_output_hash = GlobalStateValue(TealType.bytes, default=Bytes(""))
    verdict_reason_hash = GlobalStateValue(TealType.bytes, default=Bytes(""))
    oracle_verdict_at = GlobalStateValue(TealType.uint64, default=Int(0))
    status           = GlobalStateValue(TealType.uint64, default=Int(0))
    submitted_at     = GlobalStateValue(TealType.uint64, default=Int(0))
    rejected_at      = GlobalStateValue(TealType.uint64, default=Int(0))
    credit_score     = BoxMapping(abi.Address, abi.Uint64)


app = Application("BountyEscrowAgent", state=BountyState())


# ── Helper Subroutines ────────────────────────────────────────────────────────

@Subroutine(TealType.none)
def pay(receiver: Expr, amount: Expr) -> Expr:
    """Execute an inner payment transaction (fee pooling — outer pays)."""
    return InnerTxnBuilder.Execute({
        TxnField.type_enum: TxnType.Payment,
        TxnField.receiver: receiver,
        TxnField.amount: amount,
        TxnField.fee: Int(0),
    })


@Subroutine(TealType.none)
def assert_status(expected: Expr) -> Expr:
    return Assert(app.state.status == expected, comment="Wrong contract status")


@Subroutine(TealType.none)
def assert_creator() -> Expr:
    return Assert(Txn.sender() == app.state.creator, comment="Only creator")


@Subroutine(TealType.none)
def assert_contributor() -> Expr:
    return Assert(Txn.sender() == app.state.contributor, comment="Only contributor")


@Subroutine(TealType.none)
def assert_designated_oracle() -> Expr:
    return Assert(Txn.sender() == app.state.arbitrator_addr, comment="Only designated oracle")


@Subroutine(TealType.uint64)
def get_credit_score_value(account: Expr) -> Expr:
    score = abi.Uint64()
    return Seq(
        score.set(SCORE_START),
        If(app.state.credit_score[account].exists())
        .Then(app.state.credit_score[account].store_into(score)),
        score.get(),
    )


@Subroutine(TealType.none)
def set_credit_score_value(account: Expr, value: Expr) -> Expr:
    score = abi.Uint64()
    return Seq(
        score.set(value),
        app.state.credit_score[account].set(score),
    )


@Subroutine(TealType.none)
def reward_credit_score(account: Expr, delta: Expr) -> Expr:
    next_score = ScratchVar(TealType.uint64)
    return Seq(
        next_score.store(get_credit_score_value(account) + delta),
        If(next_score.load() > SCORE_MAX)
        .Then(set_credit_score_value(account, SCORE_MAX))
        .Else(set_credit_score_value(account, next_score.load())),
    )


@Subroutine(TealType.none)
def penalize_credit_score(account: Expr, delta: Expr) -> Expr:
    current_score = ScratchVar(TealType.uint64)
    return Seq(
        current_score.store(get_credit_score_value(account)),
        If(current_score.load() < delta)
        .Then(set_credit_score_value(account, Int(0)))
        .Else(set_credit_score_value(account, current_score.load() - delta)),
    )


# ── Lifecycle Methods ─────────────────────────────────────────────────────────

@app.create
def create() -> Expr:
    """Application creation — initializes empty bounty."""
    return app.state.status.set(STATUS_OPEN)


@app.external
def post_bounty(
    criteria_hash: abi.String,
    test_suite_hash: abi.String,
    deadline_unix: abi.Uint64,
    arbitrator_type: abi.String,
    arbitrator_addr: abi.Address,
    payment: abi.PaymentTransaction,
) -> Expr:
    """
    Creator posts a bounty and funds the escrow.
    Must be called with a grouped payment transaction.
    """
    return Seq(
        assert_status(STATUS_OPEN),
        Assert(Not(app.state.creator.exists()), comment="Already posted"),
        Assert(payment.get().receiver() == Global.current_application_address()),
        Assert(payment.get().amount() > Int(0)),
        Assert(deadline_unix.get() > Global.latest_timestamp()),

        app.state.creator.set(Txn.sender()),
        app.state.reward_amount.set(payment.get().amount()),
        app.state.deadline.set(deadline_unix.get()),
        app.state.criteria_hash.set(criteria_hash.get()),
        app.state.test_suite_hash.set(test_suite_hash.get()),
        app.state.arbitrator_type.set(arbitrator_type.get()),
        app.state.arbitrator_addr.set(arbitrator_addr.get()),
        app.state.submission_hash.set(Bytes("")),
        app.state.verdict_code.set(Bytes("")),
        app.state.oracle_output_hash.set(Bytes("")),
        app.state.verdict_reason_hash.set(Bytes("")),
        app.state.oracle_verdict_at.set(Int(0)),
        # Status stays OPEN — waiting for contributor
    )


@app.external
def accept_bounty() -> Expr:
    """Worker accepts an open bounty. Cannot self-accept."""
    return Seq(
        assert_status(STATUS_OPEN),
        Assert(app.state.creator.exists(), comment="Bounty not posted yet"),
        Assert(Txn.sender() != app.state.creator, comment="Creator cannot self-accept"),
        Assert(Global.latest_timestamp() < app.state.deadline, comment="Past deadline"),
        app.state.contributor.set(Txn.sender()),
        app.state.status.set(STATUS_ACCEPTED),
    )


@app.external
def submit_work(ipfs_hash: abi.String, submission_hash: abi.String) -> Expr:
    """Worker submits work plus a frozen evidence hash of the submitted artifact."""
    return Seq(
        assert_status(STATUS_ACCEPTED),
        assert_contributor(),
        Assert(Global.latest_timestamp() < app.state.deadline, comment="Past deadline"),
        Assert(Len(ipfs_hash.get()) > Int(0), comment="Empty IPFS hash"),
        Assert(Len(submission_hash.get()) == HASH_HEX_LEN, comment="Submission hash must be 64 hex chars"),
        app.state.work_ipfs_hash.set(ipfs_hash.get()),
        app.state.submission_hash.set(submission_hash.get()),
        app.state.submitted_at.set(Global.latest_timestamp()),
        app.state.status.set(STATUS_SUBMITTED),
    )


@app.external
def approve_work(contributor_account: abi.Account) -> Expr:
    """Creator approves the submitted work — triggers payout to contributor."""
    return Seq(
        assert_status(STATUS_SUBMITTED),
        assert_creator(),
        Assert(
            contributor_account.address() == app.state.contributor,
            comment="Contributor account mismatch",
        ),
        app.state.status.set(STATUS_APPROVED),
        reward_credit_score(contributor_account.address(), SCORE_APPROVED),
        pay(contributor_account.address(), app.state.reward_amount),
    )


@app.external
def reject_work() -> Expr:
    """Creator rejects the work. Starts 5-min dispute window (5 days in prod)."""
    return Seq(
        assert_status(STATUS_SUBMITTED),
        assert_creator(),
        app.state.status.set(STATUS_REJECTED),
        app.state.rejected_at.set(Global.latest_timestamp()),
    )


# ── Dispute / Opt-out ─────────────────────────────────────────────────────────

@app.external
def raise_dispute() -> Expr:
    """Contributor disputes rejection. Must be within dispute window."""
    return Seq(
        assert_status(STATUS_REJECTED),
        assert_contributor(),
        Assert(
            Global.latest_timestamp() < app.state.rejected_at + AUTO_RELEASE_DELAY,
            comment="Dispute window expired",
        ),
        app.state.status.set(STATUS_DISPUTED),
    )


@app.external
def opt_out(creator_account: abi.Account) -> Expr:
    """Contributor forfeits — funds returned to creator."""
    return Seq(
        assert_status(STATUS_REJECTED),
        assert_contributor(),
        Assert(
            creator_account.address() == app.state.creator,
            comment="Creator account mismatch",
        ),
        app.state.status.set(STATUS_OPTED_OUT),
        penalize_credit_score(app.state.contributor, SCORE_OPT_OUT),
        pay(creator_account.address(), app.state.reward_amount),
    )


@app.external
def auto_release_after_silence(contributor_account: abi.Account) -> Expr:
    """
    Anti-ghosting: if creator is silent for 5 min (5 days in prod),
    anyone can call this to release funds to contributor.
    """
    return Seq(
        assert_status(STATUS_SUBMITTED),
        Assert(
            contributor_account.address() == app.state.contributor,
            comment="Contributor account mismatch",
        ),
        Assert(
            Global.latest_timestamp() > app.state.submitted_at + AUTO_RELEASE_DELAY,
            comment="Silence period not yet passed",
        ),
        app.state.status.set(STATUS_APPROVED),
        reward_credit_score(contributor_account.address(), SCORE_AUTO_RELEASE),
        pay(contributor_account.address(), app.state.reward_amount),
    )


# ── Arbitration ───────────────────────────────────────────────────────────────

@app.external
def oracle_verdict(
    result: abi.String,
    observed_submission_hash: abi.String,
    oracle_output_hash: abi.String,
    verdict_reason_hash: abi.String,
    creator_account: abi.Account,
    contributor_account: abi.Account,
) -> Expr:
    """
    Automated oracle verdict for code bounties.
    result = "PASS" → pay contributor | "FAIL" → refund creator
    """
    return Seq(
        assert_status(STATUS_DISPUTED),
        Assert(app.state.arbitrator_type == Bytes("auto")),
        assert_designated_oracle(),
        Assert(
            Or(result.get() == Bytes("PASS"), result.get() == Bytes("FAIL")),
            comment="Verdict must be PASS or FAIL",
        ),
        Assert(
            observed_submission_hash.get() == app.state.submission_hash,
            comment="Submission evidence hash mismatch",
        ),
        Assert(Len(oracle_output_hash.get()) == HASH_HEX_LEN, comment="Oracle output hash must be 64 hex chars"),
        Assert(Len(verdict_reason_hash.get()) == HASH_HEX_LEN, comment="Verdict reason hash must be 64 hex chars"),
        Assert(
            creator_account.address() == app.state.creator,
            comment="Creator account mismatch",
        ),
        Assert(
            contributor_account.address() == app.state.contributor,
            comment="Contributor account mismatch",
        ),
        app.state.verdict_code.set(result.get()),
        app.state.oracle_output_hash.set(oracle_output_hash.get()),
        app.state.verdict_reason_hash.set(verdict_reason_hash.get()),
        app.state.oracle_verdict_at.set(Global.latest_timestamp()),
        If(result.get() == Bytes("PASS"))
        .Then(Seq(
            app.state.status.set(STATUS_RESOLVED_WORKER),
            reward_credit_score(contributor_account.address(), SCORE_DISPUTE_WIN),
            pay(contributor_account.address(), app.state.reward_amount),
        ))
        .Else(Seq(
            app.state.status.set(STATUS_RESOLVED_CREATOR),
            penalize_credit_score(contributor_account.address(), SCORE_DISPUTE_LOSS),
            pay(creator_account.address(), app.state.reward_amount),
        )),
    )


@app.external
def human_arbitrator_verdict(
    favor_contributor: abi.Bool,
    creator_account: abi.Account,
    contributor_account: abi.Account,
) -> Expr:
    """
    Human arbitrator verdict for subjective bounties.
    Only the designated arbitrator_addr can call this.
    """
    return Seq(
        assert_status(STATUS_DISPUTED),
        Assert(
            Txn.sender() == app.state.arbitrator_addr,
            comment="Only designated arbitrator",
        ),
        Assert(app.state.arbitrator_type == Bytes("human")),
        Assert(
            creator_account.address() == app.state.creator,
            comment="Creator account mismatch",
        ),
        Assert(
            contributor_account.address() == app.state.contributor,
            comment="Contributor account mismatch",
        ),
        If(favor_contributor.get())
        .Then(Seq(
            app.state.status.set(STATUS_RESOLVED_WORKER),
            reward_credit_score(contributor_account.address(), SCORE_DISPUTE_WIN),
            pay(contributor_account.address(), app.state.reward_amount),
        ))
        .Else(Seq(
            app.state.status.set(STATUS_RESOLVED_CREATOR),
            penalize_credit_score(contributor_account.address(), SCORE_DISPUTE_LOSS),
            pay(creator_account.address(), app.state.reward_amount),
        )),
    )


# ── Read-only Getters ─────────────────────────────────────────────────────────

@app.external(read_only=True)
def get_status(*, output: abi.Uint64) -> Expr:
    """Returns the current contract status (0-8)."""
    return output.set(app.state.status)


@app.external(read_only=True)
def get_credit_score(account: abi.Account, *, output: abi.Uint64) -> Expr:
    """Returns the contributor's on-chain reputation score."""
    return output.set(get_credit_score_value(account.address()))


class BountyInfo(abi.NamedTuple):
    creator: abi.Field[abi.Address]
    contributor: abi.Field[abi.Address]
    reward: abi.Field[abi.Uint64]
    deadline: abi.Field[abi.Uint64]
    criteria_hash: abi.Field[abi.String]
    work_ipfs_hash: abi.Field[abi.String]


class EvidenceInfo(abi.NamedTuple):
    submission_hash: abi.Field[abi.String]
    oracle_output_hash: abi.Field[abi.String]
    verdict_reason_hash: abi.Field[abi.String]
    verdict_code: abi.Field[abi.String]
    oracle_verdict_at: abi.Field[abi.Uint64]


@app.external(read_only=True)
def get_bounty_info(*, output: BountyInfo) -> Expr:
    """Returns full bounty details as a named tuple."""
    creator_val     = abi.Address()
    contributor_val = abi.Address()
    reward_val      = abi.Uint64()
    deadline_val    = abi.Uint64()
    criteria_val    = abi.String()
    work_hash_val   = abi.String()
    return Seq(
        creator_val.set(app.state.creator),
        contributor_val.set(app.state.contributor),
        reward_val.set(app.state.reward_amount),
        deadline_val.set(app.state.deadline),
        criteria_val.set(app.state.criteria_hash),
        work_hash_val.set(app.state.work_ipfs_hash),
        output.set(creator_val, contributor_val, reward_val, deadline_val, criteria_val, work_hash_val),
    )


@app.external(read_only=True)
def get_evidence_info(*, output: EvidenceInfo) -> Expr:
    """Returns stored evidence hashes and oracle verdict metadata."""
    submission_hash_val = abi.String()
    oracle_output_hash_val = abi.String()
    verdict_reason_hash_val = abi.String()
    verdict_code_val = abi.String()
    oracle_verdict_at_val = abi.Uint64()
    return Seq(
        submission_hash_val.set(app.state.submission_hash),
        oracle_output_hash_val.set(app.state.oracle_output_hash),
        verdict_reason_hash_val.set(app.state.verdict_reason_hash),
        verdict_code_val.set(app.state.verdict_code),
        oracle_verdict_at_val.set(app.state.oracle_verdict_at),
        output.set(
            submission_hash_val,
            oracle_output_hash_val,
            verdict_reason_hash_val,
            verdict_code_val,
            oracle_verdict_at_val,
        ),
    )


# ── Build & Export ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    artifacts_dir = os.path.join(os.path.dirname(__file__), "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    app.build().export(artifacts_dir)
    # Keep build-time logs ASCII-only for Windows consoles (default cp1252).
    print(f"Contract artifacts exported to {artifacts_dir}/")
    print("   -> approval.teal")
    print("   -> clear.teal")
    print("   -> contract.json (ABI)")
    print("   -> application.json (App Spec)")
