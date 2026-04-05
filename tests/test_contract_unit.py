"""
Offline unit tests for BountyEscrowAgent (no LocalNet, no algod).

Validates that the Beaker app builds to TEAL, exposes the expected ABI, and
declares consistent global state — fast feedback when editing contract.py.

Run from repository root:
    python -m pytest tests/test_contract_unit.py -q

Requires: pip install -r requirements-dev.txt
"""

from __future__ import annotations

import pytest
from pyteal import Mode, compileTeal

from smart_contracts.bounty_escrow.contract import app as bounty_app


@pytest.fixture(scope="module")
def spec():
    """Single build — all tests share one ApplicationSpecification."""
    return bounty_app.build()


def test_build_produces_teal_and_non_trivial_size(spec):
    approval = spec.approval_program
    clear_p = spec.clear_program
    assert isinstance(approval, str) and len(approval) > 100
    assert isinstance(clear_p, str) and len(clear_p) > 10
    # TEAL source length is not the same as bytecode length, but absurdly small
    # approval would indicate a failed or stubbed build.
    assert len(approval) > 500


def test_abi_methods_match_expected_set(spec):
    expected = {
        "create",
        "creator_cancel_bounty",
        "post_bounty",
        "accept_bounty",
        "submit_work",
        "withdraw_acceptance",
        "approve_work",
        "reject_work",
        "raise_dispute",
        "opt_out",
        "reopen_after_rejection",
        "auto_release_after_silence",
        "oracle_verdict",
        "get_status",
        "get_credit_score",
        "get_bounty_info",
        "get_evidence_info",
    }
    names = {m.name for m in spec.contract.methods}
    assert names == expected


def test_readonly_methods_flagged(spec):
    # ABI Method objects do not carry read_only; Beaker stores it on spec.hints.
    readonly = {
        sig.split("(")[0]
        for sig, hint in spec.hints.items()
        if getattr(hint, "read_only", False)
    }
    assert readonly == {
        "get_status",
        "get_credit_score",
        "get_bounty_info",
        "get_evidence_info",
    }


def test_global_state_schema_keys(spec):
    declared = spec.schema["global"]["declared"]
    keys = set(declared.keys())
    assert keys == {
        "creator",
        "contributor",
        "reward_amount",
        "deadline",
        "criteria_hash",
        "test_suite_hash",
        "work_ipfs_hash",
        "submission_hash",
        "arbitrator_type",
        "arbitrator_addr",
        "verdict_code",
        "oracle_output_hash",
        "verdict_reason_hash",
        "oracle_verdict_at",
        "status",
        "submitted_at",
        "rejected_at",
    }
    assert spec.schema["local"]["declared"] == {}


def test_global_state_types(spec):
    declared = spec.schema["global"]["declared"]
    assert declared["status"]["type"] == "uint64"
    assert declared["reward_amount"]["type"] == "uint64"
    assert declared["creator"]["type"] == "bytes"


def _teal_pushint(expr) -> int:
    """Extract a bare `Int(n)` constant from compileTeal output (application mode)."""
    teal = compileTeal(expr, mode=Mode.Application, version=8)
    for line in teal.splitlines():
        line = line.strip()
        if line.startswith("int "):
            return int(line.split()[1])
    raise AssertionError(f"no int literal in compiled TEAL:\n{teal}")


def test_state_machine_constants_documented():
    """Keep names in sync with README / architecture status codes (0–9)."""
    from smart_contracts.bounty_escrow import contract as c

    assert _teal_pushint(c.STATUS_OPEN) == 0
    assert _teal_pushint(c.STATUS_ACCEPTED) == 1
    assert _teal_pushint(c.STATUS_SUBMITTED) == 2
    assert _teal_pushint(c.STATUS_APPROVED) == 3
    assert _teal_pushint(c.STATUS_REJECTED) == 4
    assert _teal_pushint(c.STATUS_DISPUTED) == 5
    assert _teal_pushint(c.STATUS_RESOLVED_WORKER) == 6
    assert _teal_pushint(c.STATUS_RESOLVED_CREATOR) == 7
    assert _teal_pushint(c.STATUS_OPTED_OUT) == 8
    assert _teal_pushint(c.STATUS_CANCELLED) == 9


def test_auto_release_delay_localnet_seconds():
    from smart_contracts.bounty_escrow import contract as c

    assert _teal_pushint(c.AUTO_RELEASE_DELAY) == 300


def test_withdraw_accept_penalty_constant():
    from smart_contracts.bounty_escrow import contract as c

    assert _teal_pushint(c.SCORE_WITHDRAW_ACCEPT) == 5


def test_creator_cancel_penalty_constants():
    from smart_contracts.bounty_escrow import contract as c

    assert _teal_pushint(c.SCORE_CREATOR_CANCEL_ACTIVE) == 12
    assert _teal_pushint(c.SCORE_FAILED_TO_SUBMIT) == 30
