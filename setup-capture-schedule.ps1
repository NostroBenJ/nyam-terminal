# Registers the headless daily capture with Windows Task Scheduler.
#
# ASCII ONLY. PowerShell 5.1 reads this as ANSI, so a UTF-8 dash becomes three
# bytes and breaks the parser several lines later.
#
# WHY: everything that persists - the OI snapshot, your morning call, the
# grading - used to require the desktop app to be open. These tasks run the
# recorder with no GUI and no you.
#
# WHAT YOU STILL NEED: the machine has to be POWERED ON or ASLEEP. Task
# Scheduler can wake a sleeping machine (-WakeToRun below); it cannot wake one
# that is shut down or hibernated. On a laptop, plugged in is the reliable
# configuration.
#
#   powershell -ExecutionPolicy Bypass -File .\setup-capture-schedule.ps1
#   powershell -ExecutionPolicy Bypass -File .\setup-capture-schedule.ps1 -Remove
#
# ---------------------------------------------------------------------------
# THE BUG THIS VERSION FIXES, because it cost a week of captures silently.
#
# The previous version preferred `python capture.py` against the SOURCE tree
# whenever a checkout existed, and only fell back to the engine binary. Two
# faults, both invisible until you go looking:
#
#   1. Execute was the bare string "python". Task Scheduler does not resolve
#      PATH the way an interactive shell does, so every run failed instantly
#      with 0x80070002 (FILE_NOT_FOUND). Three tasks, three failures, no
#      captures, and nothing on screen said so.
#   2. Even had it run, the working directory was the source tree, so captures
#      would land in the repo's data_store while the installed app reads its
#      own. The Journal would have shown an empty recorder and you would have
#      believed the scheduler was broken rather than mis-targeted.
#
# There is now exactly ONE path: the deployed engine binary, by absolute path,
# with --capture. No interpreter to resolve, and STORE_DIR lands next to the
# app the Journal actually reads.
# ---------------------------------------------------------------------------

param(
    [switch]$Remove,
    [string]$Tickers = "SPY"
)

$ErrorActionPreference = "Stop"
$app    = $PSScriptRoot
$engine = Join-Path $app "engine\nyam-engine.exe"
$prefix = "NYAM Capture"

if (-not (Test-Path $engine)) {
    Write-Host "  Cannot find $engine" -ForegroundColor Red
    Write-Host "  Run this from the deployed app folder, not the source repo."
    exit 1
}

$tasks = @(
    @{ Name = "$prefix - Premarket"; Time = "09:20"
       Desc = "Files the morning call with its reasoning, snapshots the option chain and OI." },
    @{ Name = "$prefix - Midday";    Time = "12:05"
       Desc = "Second capture across the traded window." },
    @{ Name = "$prefix - Close";     Time = "16:20"
       Desc = "Post-close capture and grading of the day's call." }
)

if ($Remove) {
    foreach ($t in $tasks) {
        try {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction Stop
            Write-Host "  removed: $($t.Name)" -ForegroundColor Yellow
        } catch { Write-Host "  not present: $($t.Name)" -ForegroundColor DarkGray }
    }
    exit 0
}

Write-Host ""
Write-Host "  Registering NYAM capture tasks" -ForegroundColor Cyan
Write-Host "  engine : $engine"
Write-Host "  tickers: $Tickers"
Write-Host ""

foreach ($t in $tasks) {
    $action = New-ScheduledTaskAction -Execute $engine `
        -Argument "--capture --tickers $Tickers" `
        -WorkingDirectory (Join-Path $app "engine")

    # Weekdays only - the recorder skips non-trading days itself, but there is
    # no reason to wake the machine on a Saturday to learn that.
    $trigger = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
        -At ([datetime]::ParseExact($t.Time, "HH:mm", $null))

    $settings = New-ScheduledTaskSettingsSet `
        -WakeToRun `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -MultipleInstances IgnoreNew

    try {
        Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger `
            -Settings $settings -Description $t.Desc -Force | Out-Null
        Write-Host ("  {0,-12} {1}  registered" -f $t.Name.Replace("$prefix - ",""), $t.Time) -ForegroundColor Green
    } catch {
        Write-Host "  FAILED $($t.Name): $_" -ForegroundColor Red
    }
}

# PROVE IT RUNS, right now, rather than finding out on a morning that matters.
# A registered task is not a working task: the previous three were registered
# and had never once succeeded.
Write-Host ""
Write-Host "  Running one capture now to prove the wiring..." -ForegroundColor Cyan
& $engine --capture --tickers $Tickers --force
if ($LASTEXITCODE -eq 0) {
    Write-Host "  capture ran, exit 0" -ForegroundColor Green
} else {
    Write-Host "  capture FAILED, exit $LASTEXITCODE - the schedule will fail the same way" -ForegroundColor Red
}

Write-Host ""
Write-Host "  StartWhenAvailable is on: a run missed while the machine was off" -ForegroundColor DarkGray
Write-Host "  fires as soon as it next boots, so a late capture still beats none." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Check what has been captured, any time:" -ForegroundColor Cyan
Write-Host "    & '$engine' --capture --status"
Write-Host "  or open the Journal section in the app."
Write-Host ""
Read-Host "  Press Enter to close" | Out-Null
