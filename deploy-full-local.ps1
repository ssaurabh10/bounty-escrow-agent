<#
.SYNOPSIS
  Full local deploy: LocalNet, contract build + deploy (includes funding the app), Judge0 proxy, wallet/funding API, static UI.

.PARAMETER SkipLocalnet
  Do not run `algokit localnet start` (use when LocalNet is already up).

.PARAMETER WithOracle
  Also start `oracle/oracle_runner.py --poll <app_id>` (uses deploy_info.json).

.PARAMETER NoServe
  Do not start `npx serve` on port 3000.

.NOTES
  Stops processes listed in .local-stack.pids from a previous run, then writes new PIDs.
  Stop manually: .\stop-full-local.ps1
#>
param(
  [switch]$SkipLocalnet,
  [switch]$WithOracle,
  [switch]$NoServe
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$stackPidPath = Join-Path $Root ".local-stack.pids"
if (Test-Path -LiteralPath $stackPidPath) {
  Get-Content -LiteralPath $stackPidPath | ForEach-Object {
    $n = 0
    if ([int]::TryParse($_.Trim(), [ref]$n) -and $n -gt 0) {
      Stop-Process -Id $n -Force -ErrorAction SilentlyContinue
    }
  }
  Remove-Item -LiteralPath $stackPidPath -ErrorAction SilentlyContinue
}

if (-not $SkipLocalnet) {
  algokit localnet start
}

python smart_contracts/bounty_escrow/contract.py

$env:PYTHONUTF8 = "1"
try {
  python smart_contracts/bounty_escrow/deploy_config.py
} finally {
  Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
}

$npx = Get-Command npx -ErrorAction SilentlyContinue
if (-not $NoServe -and -not $npx) {
  Write-Warning "npx not found; skipping static server. Install Node.js or use -NoServe."
  $NoServe = $true
}

$pids = New-Object System.Collections.Generic.List[int]

$p = Start-Process -FilePath "node" -ArgumentList "oracle/judge0_proxy.js" -WorkingDirectory $Root -PassThru -WindowStyle Minimized
$pids.Add($p.Id) | Out-Null

$p = Start-Process -FilePath "python" -ArgumentList "oracle/localnet_wallet_api.py" -WorkingDirectory $Root -PassThru -WindowStyle Minimized
$pids.Add($p.Id) | Out-Null

if (-not $NoServe) {
  $p = Start-Process -FilePath $npx.Source -ArgumentList @("--yes", "serve", ".", "--listen", "3000") -WorkingDirectory $Root -PassThru -WindowStyle Minimized
  $pids.Add($p.Id) | Out-Null
}

if ($WithOracle) {
  $infoPath = Join-Path $Root "smart_contracts\bounty_escrow\deploy_info.json"
  $info = Get-Content $infoPath -Raw | ConvertFrom-Json
  $appId = [string]$info.app_id
  $p = Start-Process -FilePath "python" -ArgumentList @("oracle/oracle_runner.py", "--poll", $appId) -WorkingDirectory $Root -PassThru -WindowStyle Minimized
  $pids.Add($p.Id) | Out-Null
}

$pids | Set-Content -LiteralPath $stackPidPath

Write-Host ""
Write-Host "Local stack (deploy + services):"
Write-Host "  Contract deploy + app funding: done (see smart_contracts/bounty_escrow/deploy_info.json)"
Write-Host "  Judge0 proxy:    http://localhost:3456/health"
Write-Host "  Wallet / fund:   http://127.0.0.1:3457/health (prefer 127.0.0.1 on Windows)"
if (-not $NoServe) {
  Write-Host "  UI (serve):      http://localhost:3000/frontend/index.html"
}
if ($WithOracle) {
  Write-Host "  Oracle poll:     background (Piston API)"
}
Write-Host ""
Write-Host "Stop background jobs: .\stop-full-local.ps1"
Write-Host ""
