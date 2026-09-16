# Deploy the rebuilt app into the portable folder.
#
# ASCII ONLY. PowerShell 5.1 reads this file as ANSI, so a UTF-8 em-dash
# becomes three bytes that break the parser several lines later.
#
# Two things in the target must survive: engine\data_store holds daily OI
# snapshots and the track record, and an option chain cannot be re-fetched for
# a past date, so overwriting it destroys data permanently. engine\.env holds
# the API keys. Both are excluded from the mirror rather than copied over,
# because /MIR without exclusions would delete them.

$src = $PSScriptRoot
$dst = Join-Path (Split-Path $PSScriptRoot) "NYAM Terminal"
$ok  = $true

$envPath   = Join-Path $dst "engine\.env"
$storePath = Join-Path $dst "engine\data_store"
$envHashBefore = if (Test-Path $envPath) { (Get-FileHash $envPath).Hash } else { $null }
$storeBefore   = if (Test-Path $storePath) {
                   (Get-ChildItem $storePath -Recurse -File -ErrorAction SilentlyContinue).Count
                 } else { 0 }
$hb = if ($envHashBefore) { $envHashBefore.Substring(0,12) } else { "MISSING" }
"before: env_hash=$hb  data_store_files=$storeBefore"

$engineSrc = Join-Path $src "engine\dist\nyam-engine"
$engineDst = Join-Path $dst "engine"
robocopy $engineSrc $engineDst /MIR /XD data_store /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
# robocopy exit codes under 8 are success; 8 and above are real failures.
if ($LASTEXITCODE -ge 8) { "ROBOCOPY FAILED exit=$LASTEXITCODE"; $ok = $false }
else { "engine mirrored (robocopy exit=$LASTEXITCODE)" }

# The scheduler script lives in the repo and must ship WITH the app, or the
# deployed copy drifts from the one under version control - which is exactly
# how a broken `python capture.py` branch survived unnoticed for a week.
$setupSrc = Join-Path $src "setup-capture-schedule.ps1"
if (Test-Path $setupSrc) {
  Copy-Item $setupSrc (Join-Path $dst "setup-capture-schedule.ps1") -Force
  "scheduler script copied"
}

# The shell exe. VERIFY THE COPY LANDED, do not assume it.
#
# This used to test only that the SOURCE existed and then report "shell copied"
# whatever happened. With the app running, Windows holds a lock on the target,
# Copy-Item throws, and the script printed "shell copied: 8.8 MB" followed by
# "DEPLOY OK" over a frontend that had not moved. A deploy that lies about
# succeeding is worse than one that fails.
$exeSrc = Join-Path $src "src-tauri\target\release\nyam-terminal.exe"
$exeDst = Join-Path $dst "nyam-terminal.exe"
if (Test-Path $exeSrc) {
  try {
    Copy-Item $exeSrc $exeDst -Force -ErrorAction Stop
  } catch {
    "SHELL COPY FAILED: $($_.Exception.Message)"
    "  -> the app is probably running. Close NYAM Terminal and re-run."
    $ok = $false
  }
  # Hashes, not exit codes: the only question that matters is whether the file
  # on the other side is the one that was just built.
  if (Test-Path $exeDst) {
    $hs = (Get-FileHash $exeSrc).Hash
    $hd = (Get-FileHash $exeDst).Hash
    if ($hs -eq $hd) {
      $mb = [math]::Round((Get-Item $exeSrc).Length / 1MB, 1)
      "shell copied and verified: $mb MB"
    } else {
      "SHELL MISMATCH: deployed exe is NOT the one just built"; $ok = $false
    }
  } else { "SHELL MISSING at destination"; $ok = $false }
} else { "SHELL EXE MISSING at $exeSrc"; $ok = $false }

# Same question for the engine: robocopy exit codes describe the operation,
# a hash describes the result.
$engHashSrc = Join-Path $engineSrc "nyam-engine.exe"
$engHashDst = Join-Path $engineDst "nyam-engine.exe"
if ((Test-Path $engHashSrc) -and (Test-Path $engHashDst)) {
  if ((Get-FileHash $engHashSrc).Hash -eq (Get-FileHash $engHashDst).Hash) {
    "engine verified: deployed binary matches the build"
  } else { "ENGINE MISMATCH: deployed engine is NOT the one just built"; $ok = $false }
} else { "ENGINE EXE MISSING on one side"; $ok = $false }

$envHashAfter = if (Test-Path $envPath) { (Get-FileHash $envPath).Hash } else { $null }
$storeAfter   = if (Test-Path $storePath) {
                  (Get-ChildItem $storePath -Recurse -File -ErrorAction SilentlyContinue).Count
                } else { 0 }
$ha = if ($envHashAfter) { $envHashAfter.Substring(0,12) } else { "MISSING" }
"after:  env_hash=$ha  data_store_files=$storeAfter"

if ($envHashBefore -ne $envHashAfter) { "FAIL: .env changed, keys may be lost"; $ok = $false }
else { "ok: .env preserved" }
if ($storeAfter -lt $storeBefore) { "FAIL: data_store shrank $storeBefore to $storeAfter"; $ok = $false }
else { "ok: data_store preserved" }

if ($ok) { "`nDEPLOY OK" } else { "`nDEPLOY FAILED" }
