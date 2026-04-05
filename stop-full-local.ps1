# Stops processes started by deploy-full-local.ps1 (same directory).
param(
  [string]$ProjectRoot = $PSScriptRoot
)

$stackPidPath = Join-Path $ProjectRoot ".local-stack.pids"
if (-not (Test-Path -LiteralPath $stackPidPath)) {
  Write-Host "No .local-stack.pids in ${ProjectRoot} - nothing to stop."
  exit 0
}

Get-Content -LiteralPath $stackPidPath | ForEach-Object {
  $n = 0
  if ([int]::TryParse($_.Trim(), [ref]$n) -and $n -gt 0) {
    Stop-Process -Id $n -Force -ErrorAction SilentlyContinue
  }
}
Remove-Item -LiteralPath $stackPidPath -ErrorAction SilentlyContinue
Write-Host 'Stopped local stack helper processes (Judge0, wallet API, serve, optional oracle).'
