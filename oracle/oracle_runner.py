"""
Oracle / CI Runner — Bounty Escrow Agent (LocalNet Edition)
Watches contract state, runs tests via Piston API, submits verdict.

Modes:
  1. Manual:  python oracle_runner.py <code_file> <test_file> <frozen_hash>
  2. Poll:    python oracle_runner.py --poll <app_id>
  3. Verdict: python oracle_runner.py --verdict <app_id> <PASS|FAIL>
  4. Frontend: python oracle_runner.py --frontend <submission_url> <spec_file> <frozen_hash>

Prerequisites:
  - AlgoKit LocalNet running
  - Contract deployed (algokit project deploy localnet → deploy_info.json)
"""

import hashlib
import json
import re
import sys
import time

# Keep stdout/stderr UTF-8 on Windows so emoji logs don't crash.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
from algosdk.v2client import algod
from algosdk import encoding, kmd
from algosdk.atomic_transaction_composer import (
    AtomicTransactionComposer,
    AccountTransactionSigner,
)
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from smart_contracts.bounty_escrow.abi_helpers import (
    decode_app_state,
    load_contract,
    score_box_ref,
)


# ── Config ────────────────────────────────────────────────────────────────────

ALGOD_ADDRESS = "http://localhost:4001"
ALGOD_TOKEN   = "a" * 64
KMD_ADDRESS   = "http://localhost:4002"
KMD_TOKEN     = "a" * 64

PISTON_API_URL = "https://emkc.org/api/v2/piston"

POLL_INTERVAL = 5  # seconds between polls


# ── Hash Utilities ────────────────────────────────────────────────────────────

def sha256_file(path: str) -> str:
    """Return SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_string(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def decode_submission_hash_from_state(raw) -> str:
    """On-chain `submission_hash` is an ABI string (64-char hex), not a 32-byte address."""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw) if raw else ""


def verify_test_suite(local_test_path: str, frozen_hash: str) -> bool:
    """Verify test file matches the hash frozen at bounty creation."""
    actual = sha256_file(local_test_path)
    return actual == frozen_hash


# ── Piston API Runner ────────────────────────────────────────────────────────

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".cpp": "c++",
    ".c": "c",
}


def detect_language(file_path: str) -> str:
    ext = Path(file_path).suffix
    return LANGUAGE_MAP.get(ext, "python")


def run_code_piston(language: str, code: str, test_code: str) -> dict:
    """Run test suite against submitted code using Piston API."""
    combined = code + "\n\n" + test_code

    ext_map = {v: k for k, v in LANGUAGE_MAP.items()}
    ext = ext_map.get(language, ".py")

    payload = {
        "language": language,
        "version": "*",
        "files": [{"name": f"main{ext}", "content": combined}],
        "stdin": "",
        "args": [],
        "compile_timeout": 10000,
        "run_timeout": 5000,
    }

    try:
        resp = requests.post(f"{PISTON_API_URL}/execute", json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        run = result.get("run", {})
        return {
            "success": run.get("code", 1) == 0,
            "output": run.get("stdout", ""),
            "stderr": run.get("stderr", ""),
            "exit_code": run.get("code", -1),
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "output": "",
            "stderr": f"Piston API error: {e}",
            "exit_code": -1,
        }


# Frontend verification — deterministic website checks for objective UI bounties

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def extract_title(html: str) -> str:
    match = TITLE_RE.search(html or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def parse_frontend_spec(spec_text: str) -> dict:
    try:
        spec = json.loads(spec_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Frontend spec must be valid JSON: {e}") from e

    if not isinstance(spec, dict):
        raise ValueError("Frontend spec must be a JSON object")

    return {
        "expect_status": int(spec.get("expect_status", 200)),
        "required_title": str(spec.get("required_title", "")).strip(),
        "required_text": [str(v) for v in spec.get("required_text", [])],
        "forbidden_text": [str(v) for v in spec.get("forbidden_text", [])],
        "required_paths": [str(v) for v in spec.get("required_paths", [])],
        "max_response_ms": int(spec.get("max_response_ms", 8000)),
    }


def fetch_page(url: str, timeout_s: float = 12.0) -> dict:
    started = time.perf_counter()
    response = requests.get(url, timeout=timeout_s)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    content_type = response.headers.get("content-type", "")
    body = response.text if "text" in content_type or "html" in content_type else ""
    return {
        "status_code": response.status_code,
        "body": body,
        "title": extract_title(body),
        "elapsed_ms": elapsed_ms,
        "content_type": content_type,
        "final_url": response.url,
    }


def evaluate_frontend_submission(
    submission_url: str,
    frontend_spec_text: str,
    frozen_spec_hash: str,
    expected_submission_hash: str | None = None,
) -> dict:
    observed_submission_hash = sha256_string(submission_url)

    if expected_submission_hash and observed_submission_hash != expected_submission_hash:
        reason = "Submitted frontend URL hash mismatch — possible evidence drift"
        return {
            "verdict": "FAIL",
            "reason": reason,
            "submission_hash_ok": False,
            "observed_submission_hash": observed_submission_hash,
            "oracle_output_hash": sha256_string(""),
            "verdict_reason_hash": sha256_string(reason),
        }

    actual_spec_hash = sha256_string(frontend_spec_text)
    if actual_spec_hash != frozen_spec_hash:
        reason = "Frontend spec hash mismatch — possible tampering"
        return {
            "verdict": "FAIL",
            "reason": reason,
            "test_hash_ok": False,
            "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
            "observed_submission_hash": observed_submission_hash,
            "oracle_output_hash": sha256_string(""),
            "verdict_reason_hash": sha256_string(reason),
        }

    try:
        spec = parse_frontend_spec(frontend_spec_text)
    except ValueError as e:
        reason = str(e)
        return {
            "verdict": "FAIL",
            "reason": reason,
            "test_hash_ok": True,
            "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
            "observed_submission_hash": observed_submission_hash,
            "oracle_output_hash": sha256_string(""),
            "verdict_reason_hash": sha256_string(reason),
        }

    checks = []
    failures = []

    try:
        page = fetch_page(submission_url)
    except requests.RequestException as e:
        reason = f"Frontend submission could not be fetched: {e}"
        return {
            "verdict": "FAIL",
            "reason": reason,
            "test_hash_ok": True,
            "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
            "observed_submission_hash": observed_submission_hash,
            "oracle_output_hash": sha256_string(""),
            "verdict_reason_hash": sha256_string(reason),
        }

    checks.append(f"GET {submission_url} -> {page['status_code']} in {page['elapsed_ms']}ms")

    if page["status_code"] != spec["expect_status"]:
        failures.append(f"Expected status {spec['expect_status']}, got {page['status_code']}")

    if page["elapsed_ms"] > spec["max_response_ms"]:
        failures.append(
            f"Response time {page['elapsed_ms']}ms exceeded limit {spec['max_response_ms']}ms"
        )

    if spec["required_title"]:
        checks.append(f"title={page['title'] or '<missing>'}")
        if spec["required_title"] not in page["title"]:
            failures.append(f"Title did not include required text: {spec['required_title']}")

    for text in spec["required_text"]:
        if text not in page["body"]:
            failures.append(f"Missing required text: {text}")

    for text in spec["forbidden_text"]:
        if text and text in page["body"]:
            failures.append(f"Forbidden text present: {text}")

    base_url = page["final_url"] or submission_url
    for path in spec["required_paths"]:
        try:
            subpage = fetch_page(requests.compat.urljoin(base_url, path))
            checks.append(f"GET {path} -> {subpage['status_code']}")
            if subpage["status_code"] != spec["expect_status"]:
                failures.append(f"Path {path} returned {subpage['status_code']}")
        except requests.RequestException as e:
            failures.append(f"Path {path} could not be fetched: {e}")

    reason = "All frontend checks passed" if not failures else "; ".join(failures)
    oracle_output = "\n".join(checks + ([f"FAILURES: {reason}"] if failures else []))

    return {
        "verdict": "PASS" if not failures else "FAIL",
        "reason": reason,
        "test_hash_ok": True,
        "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
        "observed_submission_hash": observed_submission_hash,
        "oracle_output_hash": sha256_string(oracle_output),
        "verdict_reason_hash": sha256_string(reason),
        "checks": checks,
    }


# ── LocalNet Helpers ──────────────────────────────────────────────────────────

def parse_openapi_spec(spec_text: str) -> dict:
    try:
        spec = json.loads(spec_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Backend OpenAPI spec must be valid JSON: {e}") from e

    if not isinstance(spec, dict):
        raise ValueError("Backend OpenAPI spec must be a JSON object")

    version = str(spec.get("openapi", "")).strip()
    if not version.startswith("3."):
        raise ValueError("Backend OpenAPI spec must declare an OpenAPI 3.x version")

    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("Backend OpenAPI spec must include at least one API path")

    components = spec.get("components", {})
    security_schemes = components.get("securitySchemes", {}) if isinstance(components, dict) else {}

    return {
        "openapi": version,
        "paths": paths,
        "path_count": len(paths),
        "security_scheme_count": len(security_schemes) if isinstance(security_schemes, dict) else 0,
    }


def parse_newman_report(report_text: str) -> dict:
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Newman report must be valid JSON: {e}") from e

    run = report.get("run", {}) if isinstance(report, dict) else {}
    stats = run.get("stats", {}) if isinstance(run, dict) else {}
    assertions = stats.get("assertions", {}) if isinstance(stats, dict) else {}
    failed_assertions = int(assertions.get("failed", 0) or 0)
    total_assertions = int(assertions.get("total", 0) or 0)
    failures = run.get("failures", []) if isinstance(run, dict) else []
    failure_sources = []
    for failure in failures[:12]:
        if not isinstance(failure, dict):
            continue
        source = failure.get("source", {}) if isinstance(failure.get("source"), dict) else {}
        error = failure.get("error", {}) if isinstance(failure.get("error"), dict) else {}
        label = source.get("name") or error.get("message")
        if label:
            failure_sources.append(str(label))

    return {
        "failed_assertions": failed_assertions,
        "total_assertions": total_assertions,
        "failure_count": len(failures),
        "failure_sources": failure_sources,
    }


def parse_schemathesis_report(report_text: str) -> dict:
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Schemathesis report must be valid JSON: {e}") from e

    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    stats = report.get("stats", {}) if isinstance(report, dict) else {}
    failed_count = summary.get("failed_count") or report.get("failed_count") or stats.get("failed") or 0
    errored_count = summary.get("errored_count") or report.get("errored_count") or stats.get("errored") or 0
    case_count = summary.get("test_cases") or report.get("test_cases") or stats.get("total") or 0

    return {
        "failed_count": int(failed_count or 0),
        "errored_count": int(errored_count or 0),
        "test_cases": int(case_count or 0),
    }


def evaluate_backend_free_stack(
    openapi_spec_text: str,
    frozen_spec_hash: str,
    newman_report_text: str,
    schemathesis_report_text: str,
) -> dict:
    actual_spec_hash = sha256_string(openapi_spec_text)
    if actual_spec_hash != frozen_spec_hash:
        reason = "Backend OpenAPI spec hash mismatch — possible tampering"
        return {
            "verdict": "CREATOR_WIN",
            "reason": reason,
            "spec_hash_ok": False,
            "oracle_output_hash": sha256_string(""),
            "verdict_reason_hash": sha256_string(reason),
        }

    try:
        openapi = parse_openapi_spec(openapi_spec_text)
        newman = parse_newman_report(newman_report_text)
        schemathesis = parse_schemathesis_report(schemathesis_report_text)
    except ValueError as e:
        reason = str(e)
        return {
            "verdict": "CREATOR_WIN",
            "reason": reason,
            "spec_hash_ok": True,
            "oracle_output_hash": sha256_string(""),
            "verdict_reason_hash": sha256_string(reason),
        }

    checks = [
        f"OpenAPI version: {openapi['openapi']}",
        f"Declared paths: {openapi['path_count']}",
        f"Security schemes: {openapi['security_scheme_count']}",
        f"Newman failed assertions: {newman['failed_assertions']} / {newman['total_assertions']}",
        f"Schemathesis failures: {schemathesis['failed_count']}",
        f"Schemathesis errors: {schemathesis['errored_count']}",
    ]

    if openapi["path_count"] < 3:
        reason = "Frozen backend spec is too vague for secure automated resolution"
        oracle_output = "\n".join(checks + [f"FAILURES: {reason}"])
        return {
            "verdict": "AMBIGUOUS_SPEC",
            "reason": reason,
            "spec_hash_ok": True,
            "oracle_output_hash": sha256_string(oracle_output),
            "verdict_reason_hash": sha256_string(reason),
            "checks": checks,
        }

    score = 0
    failures = []

    if newman["failed_assertions"] == 0:
        score += 4
    elif newman["failed_assertions"] <= 2:
        score += 2
        failures.append(f"Newman reported {newman['failed_assertions']} failed assertions")
    else:
        failures.append(f"Newman reported {newman['failed_assertions']} failed assertions")

    if schemathesis["failed_count"] == 0 and schemathesis["errored_count"] == 0:
        score += 3
    elif schemathesis["failed_count"] <= 1 and schemathesis["errored_count"] == 0:
        score += 1
        failures.append("Schemathesis found minor contract mismatches")
    else:
        failures.append(
            f"Schemathesis found {schemathesis['failed_count']} failures and {schemathesis['errored_count']} errors"
        )

    if openapi["security_scheme_count"] > 0:
        score += 1
    else:
        failures.append("OpenAPI spec did not define any security scheme")

    if score >= 7:
        verdict = "CONTRIBUTOR_WIN"
        reason = "Backend submission satisfied the frozen contract and free-stack checks"
    elif score >= 4:
        verdict = "PARTIAL_SUCCESS"
        reason = "Backend submission passed core checks but still has gaps"
    else:
        verdict = "CREATOR_WIN"
        reason = "Backend submission failed critical automated checks"

    oracle_output = "\n".join(checks + ([f"FAILURES: {'; '.join(failures)}"] if failures else []))
    return {
        "verdict": verdict,
        "reason": reason,
        "score": score,
        "spec_hash_ok": True,
        "oracle_output_hash": sha256_string(oracle_output),
        "verdict_reason_hash": sha256_string(reason),
        "checks": checks,
        "newman_failure_sources": newman["failure_sources"],
    }


def get_algod():
    return algod.AlgodClient(ALGOD_TOKEN, ALGOD_ADDRESS)


def get_oracle_account():
    """Get the oracle account from KMD (uses account[2] as oracle/arbitrator)."""
    kmd_client = kmd.KMDClient(KMD_TOKEN, KMD_ADDRESS)
    wallets = kmd_client.list_wallets()
    default_wallet = next(
        (w for w in wallets if w["name"] == "unencrypted-default-wallet"), None
    )
    if not default_wallet:
        raise RuntimeError("LocalNet wallet not found")

    handle = kmd_client.init_wallet_handle(default_wallet["id"], "")
    addresses = kmd_client.list_keys(handle)

    # Use third account as oracle
    if len(addresses) < 3:
        raise RuntimeError("Need at least 3 LocalNet accounts")

    addr = addresses[2]
    pk = kmd_client.export_key(handle, "", addr)
    kmd_client.release_wallet_handle(handle)

    return {"address": addr, "private_key": pk}


# ── Oracle Logic ──────────────────────────────────────────────────────────────

class OracleRunner:
    """Oracle that evaluates code submissions and submits verdicts on-chain."""

    def __init__(self, app_id: int):
        self.app_id = app_id
        self.algod = get_algod()
        self.oracle_account = get_oracle_account()
        self.contract = load_contract()
        print(f"[Oracle] Initialized for App ID: {app_id}")
        print(f"[Oracle] Oracle address: {self.oracle_account['address'][:20]}...")

    def evaluate(
        self,
        submitted_code: str,
        test_code: str,
        frozen_test_hash: str,
        expected_submission_hash: str | None = None,
        language: str = "python",
    ) -> dict:
        """Full evaluation pipeline: verify hashes → run tests → return verdict."""
        print(f"[Oracle] Starting evaluation for app {self.app_id}")
        observed_submission_hash = sha256_string(submitted_code)

        if expected_submission_hash and observed_submission_hash != expected_submission_hash:
            reason = "Submitted artifact hash mismatch — possible evidence drift"
            return {
                "verdict": "FAIL",
                "reason": reason,
                "submission_hash_ok": False,
                "observed_submission_hash": observed_submission_hash,
                "oracle_output_hash": sha256_string(""),
                "verdict_reason_hash": sha256_string(reason),
            }

        # Step 1: Verify test suite hash
        actual_test_hash = sha256_string(test_code)
        if actual_test_hash != frozen_test_hash:
            reason = "Test suite hash mismatch — possible tampering"
            return {
                "verdict": "FAIL",
                "reason": reason,
                "test_hash_ok": False,
                "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
                "observed_submission_hash": observed_submission_hash,
                "oracle_output_hash": sha256_string(""),
                "verdict_reason_hash": sha256_string(reason),
            }
        print(f"[Oracle] ✅ Test suite hash verified: {actual_test_hash[:16]}...")

        # Step 2: Verify non-empty submission
        if not submitted_code.strip():
            reason = "Empty submission"
            return {
                "verdict": "FAIL",
                "reason": reason,
                "test_hash_ok": True,
                "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
                "observed_submission_hash": observed_submission_hash,
                "oracle_output_hash": sha256_string(""),
                "verdict_reason_hash": sha256_string(reason),
            }

        # Step 3: Run tests via Piston
        print(f"[Oracle] Running {language} tests via Piston API...")
        result = run_code_piston(language, submitted_code, test_code)

        verdict = "PASS" if result["success"] else "FAIL"
        print(f"[Oracle] Test result: {verdict}")
        if result["output"]:
            print(f"[Oracle] stdout: {result['output'][:200]}")
        if result["stderr"]:
            print(f"[Oracle] stderr: {result['stderr'][:200]}")
        oracle_output = "\n".join(
            part for part in [result.get("output", ""), result.get("stderr", "")] if part
        )
        reason = result["output"] if result["success"] else result["stderr"]

        return {
            "verdict": verdict,
            "reason": reason,
            "exit_code": result["exit_code"],
            "test_hash_ok": True,
            "submission_hash_ok": expected_submission_hash is None or observed_submission_hash == expected_submission_hash,
            "observed_submission_hash": observed_submission_hash,
            "oracle_output_hash": sha256_string(oracle_output),
            "verdict_reason_hash": sha256_string(reason),
        }

    def submit_verdict(
        self,
        verdict: str,
        observed_submission_hash: str,
        oracle_output_hash: str,
        verdict_reason_hash: str,
    ):
        """Submit oracle_verdict to the smart contract on LocalNet."""
        print(f"[Oracle] Submitting verdict '{verdict}' to app {self.app_id}...")

        atc = AtomicTransactionComposer()
        method = self.contract.get_method_by_name("oracle_verdict")
        signer = AccountTransactionSigner(self.oracle_account["private_key"])
        params = self.algod.suggested_params()
        params.fee = 2000
        params.flat_fee = True
        state = decode_app_state(self.algod, self.app_id)
        creator_addr = state.get("creator", b"")
        contributor_addr = state.get("contributor", b"")

        if isinstance(creator_addr, bytes) and len(creator_addr) == 32:
            creator_addr = encoding.encode_address(creator_addr)
        elif isinstance(creator_addr, bytes):
            creator_addr = creator_addr.decode("utf-8", errors="replace")
        if isinstance(contributor_addr, bytes) and len(contributor_addr) == 32:
            contributor_addr = encoding.encode_address(contributor_addr)
        elif isinstance(contributor_addr, bytes):
            contributor_addr = contributor_addr.decode("utf-8", errors="replace")

        atc.add_method_call(
            app_id=self.app_id,
            method=method,
            sender=self.oracle_account["address"],
            sp=params,
            signer=signer,
            method_args=[
                verdict,
                observed_submission_hash,
                oracle_output_hash,
                verdict_reason_hash,
                creator_addr,
                contributor_addr,
            ],
            accounts=[creator_addr, contributor_addr],
            boxes=[score_box_ref(self.app_id, contributor_addr)],
        )

        result = atc.execute(self.algod, 4)
        tx_id = result.tx_ids[0]
        print(f"[Oracle] ✅ Verdict submitted! TxID: {tx_id}")
        return tx_id

    def poll_and_evaluate(self):
        """
        Poll mode: watch contract state, auto-evaluate when DISPUTED + auto.
        Runs in a loop until Ctrl+C.
        """
        print(f"[Oracle] 🔄 Starting poll mode (every {POLL_INTERVAL}s)...")
        print(f"[Oracle] Watching App ID: {self.app_id}")
        print(f"[Oracle] Press Ctrl+C to stop\n")

        last_status = -1

        while True:
            try:
                state = decode_app_state(self.algod, self.app_id)
                status = state.get("status", 0)
                arb_type = state.get("arbitrator_type", b"auto")

                if isinstance(arb_type, bytes):
                    arb_type = arb_type.decode("utf-8", errors="replace")

                if status != last_status:
                    status_labels = {
                        0: "OPEN", 1: "ACCEPTED", 2: "SUBMITTED", 3: "APPROVED",
                        4: "REJECTED", 5: "DISPUTED", 6: "RESOLVED_WORKER",
                        7: "RESOLVED_CREATOR", 8: "OPTED_OUT", 9: "CANCELLED",
                    }
                    print(f"[Oracle] Status changed: {status_labels.get(status, '?')} ({status})")
                    last_status = status

                # Auto-evaluate when disputed + auto arbitrator
                if status == 5 and arb_type == "auto":
                    print(f"[Oracle] 🎯 DISPUTED state detected with auto arbitrator!")
                    print(f"[Oracle] In production, would fetch IPFS content and run tests.")
                    print(f"[Oracle] For demo: submitting a recorded PASS verdict against the stored evidence hash...")

                    # In a real implementation, we'd:
                    # 1. Fetch work_ipfs_hash content from IPFS
                    # 2. Fetch test suite from IPFS
                    # 3. Verify test suite hash
                    # 4. Run tests via Piston
                    # For demo, we just submit PASS
                    try:
                        submission_hash = decode_submission_hash_from_state(
                            state.get("submission_hash", b"")
                        )
                        demo_reason = "Demo oracle poll mode: no off-chain fetch configured, using stored submission hash."
                        self.submit_verdict(
                            "PASS",
                            submission_hash,
                            sha256_string(demo_reason),
                            sha256_string(demo_reason),
                        )
                        print(f"[Oracle] ✅ Auto-evaluation complete!")
                    except Exception as e:
                        print(f"[Oracle] ❌ Verdict submission failed: {e}")

                time.sleep(POLL_INTERVAL)

            except KeyboardInterrupt:
                print(f"\n[Oracle] 🛑 Poll mode stopped.")
                break
            except Exception as e:
                print(f"[Oracle] ⚠️ Error: {e}")
                time.sleep(POLL_INTERVAL)


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Bounty Escrow Oracle — LocalNet Edition")
        print()
        print("Usage:")
        print("  Manual evaluation:")
        print("    python oracle_runner.py <code_file> <test_file> <frozen_hash>")
        print()
        print("  Auto-poll mode (watches contract, auto-evaluates disputes):")
        print("    python oracle_runner.py --poll <app_id>")
        print()
        print("  Submit verdict directly:")
        print("    python oracle_runner.py --verdict <app_id> <PASS|FAIL>")
        print()
        print("  Frontend website evaluation:")
        print("    python oracle_runner.py --frontend <submission_url> <spec_file> <frozen_hash>")
        print()
        print("  Backend dispute evaluation (free stack):")
        print("    python oracle_runner.py --backend-free <openapi_spec.json> <newman_report.json> <schemathesis_report.json> <frozen_hash>")
        sys.exit(0)

    if sys.argv[1] == "--poll":
        if len(sys.argv) < 3:
            print("Usage: python oracle_runner.py --poll <app_id>")
            sys.exit(1)
        app_id = int(sys.argv[2])
        runner = OracleRunner(app_id)
        runner.poll_and_evaluate()

    elif sys.argv[1] == "--verdict":
        if len(sys.argv) < 4:
            print("Usage: python oracle_runner.py --verdict <app_id> <PASS|FAIL>")
            sys.exit(1)
        app_id = int(sys.argv[2])
        verdict = sys.argv[3].upper()
        if verdict not in ("PASS", "FAIL"):
            print("Verdict must be PASS or FAIL")
            sys.exit(1)
        runner = OracleRunner(app_id)
        state = decode_app_state(runner.algod, app_id)
        submission_hash = decode_submission_hash_from_state(state.get("submission_hash", b""))
        reason = f"Manual oracle verdict submitted from CLI: {verdict}"
        runner.submit_verdict(
            verdict,
            submission_hash,
            sha256_string(reason),
            sha256_string(reason),
        )

    elif sys.argv[1] == "--frontend":
        if len(sys.argv) < 5:
            print("Usage: python oracle_runner.py --frontend <submission_url> <spec_file> <frozen_hash>")
            sys.exit(1)

        submission_url = sys.argv[2]
        spec_path = sys.argv[3]
        frozen_hash = sys.argv[4]

        with open(spec_path, encoding="utf-8") as f:
            frontend_spec_text = f.read()

        result = evaluate_frontend_submission(
            submission_url,
            frontend_spec_text,
            frozen_hash,
        )
        print(json.dumps(result, indent=2))

    elif sys.argv[1] == "--backend-free":
        if len(sys.argv) < 6:
            print("Usage: python oracle_runner.py --backend-free <openapi_spec.json> <newman_report.json> <schemathesis_report.json> <frozen_hash>")
            sys.exit(1)

        openapi_path = sys.argv[2]
        newman_path = sys.argv[3]
        schemathesis_path = sys.argv[4]
        frozen_hash = sys.argv[5]

        with open(openapi_path, encoding="utf-8") as f:
            openapi_spec_text = f.read()
        with open(newman_path, encoding="utf-8") as f:
            newman_report_text = f.read()
        with open(schemathesis_path, encoding="utf-8") as f:
            schemathesis_report_text = f.read()

        result = evaluate_backend_free_stack(
            openapi_spec_text,
            frozen_hash,
            newman_report_text,
            schemathesis_report_text,
        )
        print(json.dumps(result, indent=2))

    else:
        # Manual evaluation mode
        if len(sys.argv) < 4:
            print("Usage: python oracle_runner.py <code_file> <test_file> <frozen_hash>")
            sys.exit(1)

        code_path = sys.argv[1]
        test_path = sys.argv[2]
        frozen_hash = sys.argv[3]

        with open(code_path) as f:
            submitted_code = f.read()
        with open(test_path) as f:
            test_code = f.read()

        language = detect_language(code_path)

        runner = OracleRunner(app_id=0)
        result = runner.evaluate(submitted_code, test_code, frozen_hash, language=language)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
