# Bounty Escrow Agent — AlgoKit LocalNet Edition

**Team Marcos.dev | BIT Sindri | Hackatron 3.0**

> Decentralized bounty escrow with oracle-only dispute resolution for objective, machine-verifiable bounties.

---

## Quick Start

Run everything from the **repository root** so paths and `.algokit.toml` match what AlgoKit expects.

```bash
# 1. Start LocalNet (requires Docker Desktop)
algokit localnet start

# 2. Build smart contract artifacts (TEAL + ABI under smart_contracts/bounty_escrow/artifacts)
python smart_contracts/bounty_escrow/contract.py

# 3. Deploy to LocalNet (uses [project.deploy] in .algokit.toml)
algokit project deploy localnet

# In CI or scripts, use non-interactive mode:
# algokit project deploy localnet --non-interactive

# 4. Run the full test suite (needs LocalNet + deploy for E2E cases)
python -m pytest -q

# 5. Demo UI
start frontend/index.html

# On-chain UI (KMD / Pera signing — serve repo root so ABI loads)
```

**Offline contract checks (no chain):** after `pip install -r requirements-dev.txt`, run `python -m pytest tests/test_contract_unit.py -q`. These validate TEAL build, ABI shape, and status constants—useful before E2E.

**Direct deploy without AlgoKit:** `python smart_contracts/bounty_escrow/deploy_config.py` runs the same script; prefer `algokit project deploy localnet` so demos and automation share one entry point.

On Windows, if `algokit project deploy localnet` fails with a Unicode/console error, use `PYTHONUTF8=1` (PowerShell: `$env:PYTHONUTF8='1'`) before the Python deploy script, or run the full stack script below.

### Full local stack (chain + funding API + Judge0 + UI)

One command starts **LocalNet**, **builds and deploys** the contract (including **funding the app account**), then runs:

| Service | Port | Role |
|--------|------|------|
| Judge0 proxy | 3456 | Code execution from the browser (`node oracle/judge0_proxy.js`) |
| Wallet / faucet API | 3457 | List KMD accounts and fund addresses (`python oracle/localnet_wallet_api.py`) |
| Static site | 3000 | `npx serve .` — open `/frontend/index.html` |

From the project directory:

```powershell
.\deploy-full-local.ps1
```

Optional: `-SkipLocalnet` if LocalNet is already running; `-WithOracle` to also start `oracle_runner.py --poll <app_id>`; `-NoServe` if you only want chain + proxies.

Stop the background helpers:

```powershell
.\stop-full-local.ps1
```

Double-click: `deploy-full-local.cmd`

---

## GitHub CI Pipeline (Lint + Type + AI + Security + Bot Verdict)

This repo now includes `.github/workflows/code_check.yml` with:
- `flake8` (style + errors)
- `pylint` (quality scoring)
- `mypy` (type checking)
- `pytest` (targeted tests)
- security reality check (fails on likely hardcoded secrets)
- AI test-case generator
- automated PR bot comment with final verdict (`PASS` / `FAIL`)

### Setup in GitHub

1. Push this repository to GitHub.
2. Open **Settings → Secrets and variables → Actions**.
3. Add secret: `OPENAI_API_KEY` (optional; if missing, AI generator uses deterministic fallback).
4. Open a Pull Request.
5. Check the workflow run named **Code Quality Check**.
6. Read bot verdict in PR comments.

Security principle: never commit API keys. Keep secrets only in GitHub Secrets.

---

## Project Structure

```
Bounty Escrow Agent/
├── smart_contracts/
│   └── bounty_escrow/
│       ├── contract.py          # Beaker/PyTeal smart contract
│       ├── deploy_config.py     # LocalNet deployment (KMD accounts)
│       ├── artifacts/           # Generated TEAL + ABI JSON
│       └── deploy_info.json     # Output: app_id, addresses
├── oracle/
│   └── oracle_runner.py         # Piston API oracle (poll + manual modes)
├── frontend/
│   ├── index.html               # Demo UI (localStorage; Pera optional for login)
│   ├── chain-desk.html          # On-chain ops (KMD LocalNet + Pera TestNet)
│   └── src/
│       ├── algoClient.js        # AlgoSDK + KMD wallet client
│       ├── ipfsUtils.js         # Mock IPFS (localStorage)
│       └── creditScore.js       # CIBIL-inspired scoring
├── tests/
│   ├── conftest.py              # Pytest path setup
│   ├── test_contract_unit.py    # Offline: build / ABI / schema (pytest)
│   └── test_bounty_escrow.py    # E2E on LocalNet (5 scenarios)
├── .algokit.toml                # AlgoKit project config
├── .env.localnet                # LocalNet endpoints
├── deploy-full-local.ps1        # LocalNet + deploy + Judge0 + wallet API + serve
├── deploy-full-local.cmd        # Launcher for the above (Windows)
├── stop-full-local.ps1          # Stop helpers started by deploy-full-local.ps1
├── requirements-dev.txt         # pytest (optional, for unit tests)
└── README.md
```

---

## Status Machine

```
OPEN → ACCEPTED → SUBMITTED → APPROVED  ✅ (creator approves)
                            ↘ REJECTED  ❌ (creator rejects)
                                  ↓
                    ┌─────────────┴──────────────┐
                    ↓                            ↓
               DISPUTED ⚖️                  OPTED_OUT 🚪
               (dispute raised)           (contributor walks)
                    ↓
         ┌──────────┴──────────┐
         ↓                     ↓
  RESOLVED_WORKER 🏆    RESOLVED_CREATOR
  (contributor wins)    (creator wins)

Also: SUBMITTED ──5 min silence──→ AUTO_RELEASE → APPROVED
```

**Note:** Timers are 5 minutes on LocalNet (5 days in production).

---

## Oracle Usage

```bash
# Auto-poll mode (watches contract, auto-resolves disputes)
python oracle/oracle_runner.py --poll <app_id>

# Manual verdict submission
python oracle/oracle_runner.py --verdict <app_id> PASS
python oracle/oracle_runner.py --verdict <app_id> FAIL

# Evaluate code files
python oracle/oracle_runner.py solution.py tests.py <frozen_hash>

# Evaluate a backend dispute with the free stack
python oracle/oracle_runner.py --backend-free oracle/backend_openapi_example.json oracle/backend_newman_report_example.json oracle/backend_schemathesis_report_example.json <frozen_hash>
```

Free backend-dispute example artifacts now live under [oracle](C:\Users\ssaur\Downloads\AntiGravity\Bounty Escrow Agent\oracle):
- `backend_bounty_schema.json`
- `backend_bounty_example.json`
- `backend_openapi_example.json`
- `backend_postman_collection_example.json`
- `backend_newman_report_example.json`
- `backend_schemathesis_report_example.json`
- `backend_free_stack_workflow.md`

---

## LocalNet Accounts

| Role        | Source        | Purpose                     |
|-------------|---------------|-----------------------------|
| Creator     | KMD Account 0 | Posts + funds bounties       |
| Worker      | KMD Account 1 | Accepts + submits work       |
| Oracle      | KMD Account 2 | Automated dispute resolution |

All accounts are pre-funded with ALGO on LocalNet.

---

## Key Differences from Production

| Feature          | LocalNet              | Production (TestNet)     |
|------------------|-----------------------|--------------------------|
| Timer duration   | 5 minutes             | 5 days                   |
| Wallet           | KMD (auto-sign)       | Pera Wallet (user signs) |
| IPFS             | localStorage mock     | Infura/Pinata IPFS       |
| Oracle auth      | Designated oracle only | Designated oracle only  |
| Transaction conf | Instant (~4s)         | ~4.5 seconds             |

---

## Credit Score Formula

```
raw   = completions*100 + disputes_won*50 - disputes_lost*80 - opted_out*30
score = clamp(300 + (raw / max_expected) * 600, 300, 900)
```

| Tier     | Range   | Access Level              |
|----------|---------|---------------------------|
| Unranked | 300–449 | Basic bounties only       |
| Bronze   | 450–599 | Standard bounties         |
| Silver   | 600–699 | Mid-tier bounties         |
| Gold     | 700–799 | All bounties + priority   |
| Platinum | 800–900 | Premium bounties + mentor |
