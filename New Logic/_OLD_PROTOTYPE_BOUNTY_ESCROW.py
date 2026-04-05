# Bounty Escrow Contract with Credit Score + Final State (Completed)

from beaker import *
from beaker.lib.storage import BoxMapping
from pyteal import *

app = Application("BountyEscrow")

# ── Constants ─────────────────────────────────────
THREE_DAYS = Int(3 * 24 * 60 * 60)
FIVE_DAYS = Int(5 * 24 * 60 * 60)

# Score deltas
SCORE_START = Int(100)
SCORE_APPROVED = Int(50)
SCORE_AUTO = Int(20)
SCORE_DISPUTE_WIN = Int(30)
SCORE_DISPUTE_LOSS_AMT = Int(80)
SCORE_OPT_OUT_AMT = Int(10)
SCORE_EXPIRE_AMT = Int(30)

# ── State ─────────────────────────────────────────
class AppState:
    creator = GlobalStateValue(TealType.bytes)
    admin = GlobalStateValue(TealType.bytes)
    contributor = GlobalStateValue(TealType.bytes)
    reward_amount = GlobalStateValue(TealType.uint64)
    deadline = GlobalStateValue(TealType.uint64)
    criteria_hash = GlobalStateValue(TealType.bytes)
    test_suite_hash = GlobalStateValue(TealType.bytes)
    work_ipfs_hash = GlobalStateValue(TealType.bytes)
    status = GlobalStateValue(TealType.uint64)
    submitted_at = GlobalStateValue(TealType.uint64)
    rejected_at = GlobalStateValue(TealType.uint64)

    # Credit score mapping
    credit_score = BoxMapping(TealType.bytes, TealType.uint64)

app.state = AppState()

# ── Status Enum ───────────────────────────────────
STATUS_OPEN = Int(0)
STATUS_ACCEPTED = Int(1)
STATUS_SUBMITTED = Int(2)
STATUS_APPROVED = Int(3)
STATUS_REJECTED = Int(4)
STATUS_DISPUTED = Int(5)
STATUS_DISSOLVED_CANCELLED = Int(10)
STATUS_DISSOLVED_SETTLED = Int(11)

# ── Security ──────────────────────────────────────
@Subroutine(TealType.none)
def assert_no_rekey():
    return Assert(Txn.rekey_to() == Global.zero_address())

# ── Score Helpers ─────────────────────────────────
@Subroutine(TealType.uint64)
def get_score(addr):
    return app.state.credit_score[addr].get(default=SCORE_START)

@Subroutine(TealType.none)
def add_score(addr, delta):
    new_score = get_score(addr) + delta
    return app.state.credit_score[addr].set(
        If(new_score > Int(1000), Int(1000), new_score)
    )

@Subroutine(TealType.none)
def sub_score(addr, delta):
    cur_score = get_score(addr)
    return app.state.credit_score[addr].set(
        If(cur_score < delta, Int(0), cur_score - delta)
    )

# ── Helpers ───────────────────────────────────────
@Subroutine(TealType.none)
def assert_creator():
    return Assert(Txn.sender() == app.state.creator.get())

@Subroutine(TealType.none)
def assert_contributor():
    return Assert(Txn.sender() == app.state.contributor.get())

@Subroutine(TealType.none)
def assert_status(s):
    return Assert(app.state.status.get() == s)

# ── Core Functions ────────────────────────────────

@app.external
def post_bounty(criteria_hash: abi.String, test_suite_hash: abi.String, deadline: abi.Uint64):
    return Seq(
        assert_no_rekey(),
        Assert(Len(criteria_hash.get()) == Int(64)),
        Assert(Len(test_suite_hash.get()) == Int(64)),

        app.state.creator.set(Txn.sender()),
        app.state.admin.set(Txn.sender()),
        app.state.criteria_hash.set(criteria_hash.get()),
        app.state.test_suite_hash.set(test_suite_hash.get()),
        app.state.deadline.set(deadline.get()),
        app.state.status.set(STATUS_OPEN),
    )

@app.external
def accept_bounty():
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_OPEN),
        app.state.contributor.set(Txn.sender()),
        app.state.status.set(STATUS_ACCEPTED),
    )

@app.external
def submit_work(ipfs_hash: abi.String):
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_ACCEPTED),
        assert_contributor(),
        Assert(app.state.work_ipfs_hash.get() == Bytes("")),
        Assert(Len(ipfs_hash.get()) > Int(10)),

        app.state.work_ipfs_hash.set(ipfs_hash.get()),
        app.state.submitted_at.set(Global.latest_timestamp()),
        app.state.status.set(STATUS_SUBMITTED),
    )

@app.external
def approve_work():
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_SUBMITTED),
        assert_creator(),

        add_score(app.state.contributor.get(), SCORE_APPROVED),

        # payout would occur here

        app.state.status.set(STATUS_DISSOLVED_SETTLED),
    )

@app.external
def reject_work():
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_SUBMITTED),
        assert_creator(),

        app.state.rejected_at.set(Global.latest_timestamp()),
        app.state.status.set(STATUS_REJECTED),
    )

@app.external
def raise_dispute():
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_REJECTED),
        assert_contributor(),

        Assert(Global.latest_timestamp() < app.state.rejected_at.get() + THREE_DAYS),

        app.state.status.set(STATUS_DISPUTED),
    )

@app.external
def opt_out():
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_REJECTED),
        assert_contributor(),

        Assert(Global.latest_timestamp() < app.state.rejected_at.get() + THREE_DAYS),

        # withdraw penalty
        sub_score(app.state.contributor.get(), SCORE_OPT_OUT_AMT),

        app.state.status.set(STATUS_DISSOLVED_CANCELLED),
    )

@app.external
def auto_release_after_silence():
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_SUBMITTED),
        Assert(app.state.contributor.get() != Bytes("")),

        Assert(Global.latest_timestamp() > app.state.submitted_at.get() + FIVE_DAYS),

        add_score(app.state.contributor.get(), SCORE_AUTO),

        app.state.status.set(STATUS_DISSOLVED_SETTLED),
    )

@app.external
def expire_bounty():
    return Seq(
        assert_no_rekey(),

        # only creator or admin
        Assert(
            Or(
                Txn.sender() == app.state.creator.get(),
                Txn.sender() == app.state.admin.get()
            )
        ),

        assert_status(STATUS_ACCEPTED),

        Assert(Global.latest_timestamp() > app.state.deadline.get()),
        Assert(app.state.work_ipfs_hash.get() == Bytes("")),

        # abandon penalty
        sub_score(app.state.contributor.get(), SCORE_EXPIRE_AMT),

        app.state.contributor.set(Bytes("")),
        app.state.work_ipfs_hash.set(Bytes("")),
        app.state.status.set(STATUS_OPEN),
    )

@app.external
def oracle_verdict(pass_flag: abi.String):
    return Seq(
        assert_no_rekey(),
        assert_status(STATUS_DISPUTED),

        If(pass_flag.get() == Bytes("PASS"))
        .Then(
            Seq(
                add_score(app.state.contributor.get(), SCORE_DISPUTE_WIN),
                app.state.status.set(STATUS_DISSOLVED_SETTLED)
            )
        )
        .Else(
            Seq(
                sub_score(app.state.contributor.get(), SCORE_DISPUTE_LOSS_AMT),
                app.state.status.set(STATUS_DISSOLVED_CANCELLED)
            )
        )
    )

# ── Build ─────────────────────────────────────────
if __name__ == "__main__":
    app.build().export("./artifacts")
