"""
LocalNet Wallet API — Bounty Escrow Agent
========================================
Small local HTTP API to:
- list AlgoKit LocalNet KMD accounts
- fund an address from account[0] (faucet-style)

Run:
  python oracle/localnet_wallet_api.py

Endpoints:
  GET  /health
  GET  /accounts
  GET  /balance?address=...   (algod account balance, microAlgos)
  POST /fund   {"address": "...", "microalgos": 1000000}

Notes:
- Intended for local development only.
- Requires AlgoKit LocalNet running (algod on :4001, kmd on :4002).
- Listens on IPv6 :: (dual-stack) when supported, else 0.0.0.0 — use http://127.0.0.1:PORT
  from the browser on Windows so you do not hit IPv6-only “localhost” issues.
- GET /health reports algod_reachable and kmd_reachable for quick debugging.
"""

from __future__ import annotations

import json
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from algosdk import encoding, kmd
from algosdk.transaction import PaymentTxn, wait_for_confirmation
from algosdk.v2client import algod


ALGOD_ADDRESS = "http://localhost:4001"
ALGOD_TOKEN = "a" * 64
KMD_ADDRESS = "http://localhost:4002"
KMD_TOKEN = "a" * 64

PORT = 3457


if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _json(obj: Any) -> bytes:
    return json.dumps(obj, indent=2).encode("utf-8")


def get_kmd_accounts() -> list[dict[str, str]]:
    kmd_client = kmd.KMDClient(KMD_TOKEN, KMD_ADDRESS)
    wallets = kmd_client.list_wallets()
    default_wallet = next((w for w in wallets if w["name"] == "unencrypted-default-wallet"), None)
    if not default_wallet:
        raise RuntimeError("LocalNet default KMD wallet not found. Is LocalNet running?")

    handle = kmd_client.init_wallet_handle(default_wallet["id"], "")
    try:
        addrs = kmd_client.list_keys(handle)
        accounts: list[dict[str, str]] = []
        for a in addrs:
            accounts.append({"address": a})
        return accounts
    finally:
        kmd_client.release_wallet_handle(handle)


def export_private_key_for_address(address: str) -> bytes:
    kmd_client = kmd.KMDClient(KMD_TOKEN, KMD_ADDRESS)
    wallets = kmd_client.list_wallets()
    default_wallet = next((w for w in wallets if w["name"] == "unencrypted-default-wallet"), None)
    if not default_wallet:
        raise RuntimeError("LocalNet default KMD wallet not found. Is LocalNet running?")

    handle = kmd_client.init_wallet_handle(default_wallet["id"], "")
    try:
        return kmd_client.export_key(handle, "", address)
    finally:
        kmd_client.release_wallet_handle(handle)


def _algod_ping() -> tuple[bool, str | None]:
    try:
        c = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)
        c.status()
        return True, None
    except Exception as e:
        return False, str(e)


def _kmd_ping() -> tuple[bool, str | None]:
    try:
        kmd_client = kmd.KMDClient(KMD_TOKEN, KMD_ADDRESS)
        kmd_client.list_wallets()
        return True, None
    except Exception as e:
        return False, str(e)


def fund_address(receiver: str, microalgos: int) -> dict[str, Any]:
    if not encoding.is_valid_address(receiver):
        raise ValueError("Invalid Algorand address")
    if microalgos <= 0:
        raise ValueError("microalgos must be > 0")

    algod_client = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)

    # Use account[0] as faucet
    accounts = get_kmd_accounts()
    if not accounts:
        raise RuntimeError("No KMD accounts available")
    faucet_addr = accounts[0]["address"]
    faucet_pk = export_private_key_for_address(faucet_addr)

    params = algod_client.suggested_params()
    txn = PaymentTxn(sender=faucet_addr, sp=params, receiver=receiver, amt=int(microalgos))
    stxn = txn.sign(faucet_pk)
    txid = algod_client.send_transaction(stxn)
    wait_for_confirmation(algod_client, txid, 4)

    info = algod_client.account_info(receiver)
    bal = int(info.get("amount", 0))
    return {"txid": txid, "receiver": receiver, "microalgos": microalgos, "receiver_balance": bal}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[LocalNetWalletAPI] %s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path_norm = parsed.path.rstrip("/") or "/"
            if path_norm == "/health":
                algod_ok, algod_err = _algod_ping()
                kmd_ok, kmd_err = _kmd_ping()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    _json(
                        {
                            "status": "ok",
                            "service": "localnet_wallet_api",
                            "port": PORT,
                            "algod": ALGOD_ADDRESS,
                            "kmd": KMD_ADDRESS,
                            "algod_reachable": algod_ok,
                            "algod_error": algod_err,
                            "kmd_reachable": kmd_ok,
                            "kmd_error": kmd_err,
                        }
                    )
                )
                return

            if path_norm == "/accounts" and not parsed.query:
                accounts = get_kmd_accounts()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json({"accounts": accounts}))
                return

            if path_norm == "/balance":
                qs = parse_qs(parsed.query or "")
                address = (qs.get("address") or [""])[0].strip()
                if not address or not encoding.is_valid_address(address):
                    self.send_response(400)
                    self._cors()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(_json({"error": "Invalid or missing address"}))
                    return
                algod_client = algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)
                info = algod_client.account_info(address)
                micro = int(info.get("amount", 0))
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    _json(
                        {
                            "address": address,
                            "microalgos": micro,
                            "algos": micro / 1_000_000,
                        }
                    )
                )
                return

            self.send_response(404)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_json({"error": "Not found"}))
        except Exception as e:
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_json({"error": str(e)}))

    def do_POST(self):
        try:
            post_path = urlparse(self.path).path.rstrip("/") or "/"
            if post_path != "/fund":
                self.send_response(404)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json({"error": "Not found"}))
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError as je:
                self.send_response(400)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json({"error": "Invalid JSON body", "detail": str(je)}))
                return

            receiver = str(payload.get("address", "")).strip()
            microalgos = int(payload.get("microalgos", 0))
            result = fund_address(receiver, microalgos)

            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_json(result))
        except Exception as e:
            self.send_response(500)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(_json({"error": str(e)}))


def main() -> None:
    # Prefer IPv6 dual-stack so both http://127.0.0.1:* and http://localhost:* work on Windows.
    try:

        class V6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server = V6ThreadingHTTPServer(("::", PORT), Handler)
        server.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        bind_desc = f":::{PORT} (IPv6 dual-stack; IPv4-mapped)"
    except OSError:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
        bind_desc = f"0.0.0.0:{PORT} (IPv4 all interfaces)"
    print(f"[LocalNetWalletAPI] Listening on {bind_desc}")
    print(f"[LocalNetWalletAPI] Try: http://127.0.0.1:{PORT}/health")
    server.serve_forever()


if __name__ == "__main__":
    main()

