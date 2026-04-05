"""Shared helpers for loading and decoding the exported bounty escrow ABI."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from algosdk import encoding
from algosdk.abi import Contract


CONTRACT_JSON_PATH = Path(__file__).resolve().parent / "artifacts" / "contract.json"


def load_contract_spec() -> dict[str, Any]:
    with CONTRACT_JSON_PATH.open(encoding="utf-8") as contract_file:
        return json.load(contract_file)


def load_contract() -> Contract:
    return Contract.from_json(json.dumps(load_contract_spec()))


def decode_app_state(algod_client, app_id: int) -> dict[str, Any]:
    """Read and decode application global state using UTF-8 keys."""
    info = algod_client.application_info(app_id)
    state: dict[str, Any] = {}

    for kv in info["params"].get("global-state", []):
        key = base64.b64decode(kv["key"]).decode("utf-8", errors="replace")
        value = kv["value"]
        if value["type"] == 1:
            state[key] = base64.b64decode(value.get("bytes", ""))
        else:
            state[key] = value.get("uint", 0)

    return state


def score_box_name(address: str) -> bytes:
    """Contract box key for a participant reputation score."""
    return encoding.decode_address(address)


def score_box_ref(app_id: int, address: str) -> tuple[int, bytes]:
    return (app_id, score_box_name(address))
