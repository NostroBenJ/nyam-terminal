# Update a secret in the app's .env without it ever appearing in a chat,
# a shell history, or the terminal window.
#
#   Right-click -> Run with PowerShell,  or:  .\set-key.ps1
#
# Read-Host -AsSecureString means the key is not echoed as you paste it and
# does not land in PSReadLine history the way a typed command would.

$ErrorActionPreference = "Stop"
$envPath = Join-Path $PSScriptRoot "engine\.env"

Write-Host ""
Write-Host "  NYAM Terminal - set an API key" -ForegroundColor Cyan
Write-Host "  $envPath"
Write-Host ""
Write-Host "  1) ANTHROPIC_API_KEY   (chat + written brief)"
Write-Host "  2) UW_API_KEY          (flow, dark pool, real-time chains)"
Write-Host ""
$choice = Read-Host "  Which key? [1/2]"
$name = if ($choice -eq "2") { "UW_API_KEY" } else { "ANTHROPIC_API_KEY" }

$secure = Read-Host "  Paste the new $name (input is hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $value = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if ([string]::IsNullOrWhiteSpace($value)) { Write-Host "  Nothing entered - no change." -ForegroundColor Yellow; exit 1 }

# Preserve every other line; replace only this key's line, or append it.
$lines = if (Test-Path $envPath) { @(Get-Content $envPath) } else { @() }
$found = $false
$out = foreach ($l in $lines) {
    if ($l -match "^\s*$name\s*=") { $found = $true; "$name=$value" } else { $l }
}
if (-not $found) { $out += "$name=$value" }

$dir = Split-Path $envPath
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
Set-Content -Path $envPath -Value $out -Encoding utf8

Write-Host ""
Write-Host "  $name updated ($($value.Length) chars)." -ForegroundColor Green
Write-Host "  Restart NYAM Terminal to pick it up - the engine reads .env at startup."
Write-Host ""
Read-Host "  Press Enter to close" | Out-Null
