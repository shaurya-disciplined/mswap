<#
.SYNOPSIS
    Guided, safe human-run spike for Antigravity live switch behaviors (U1, U2, U6, U7).
.DESCRIPTION
    Tests how a running agy process responds to credential changes, whether it writes back
    credentials upon token refresh, whether deleting the live credential produces a clean
    sign-in prompt, and optionally whether /logout revokes the refresh token server-side.
    Must be run in an independent terminal, outside agy.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# 0. Safety Guard: Disallow running inside agy
# ---------------------------------------------------------------------------
function Test-InsideAgy {
    $currentPid = $PID
    $hop = 0
    while ($currentPid -and $hop -lt 64) {
        $filter = "ProcessId = $currentPid"
        $proc = Get-CimInstance -ClassName Win32_Process -Filter $filter -ErrorAction SilentlyContinue
        if (-not $proc) { break }
        $parentPid = $proc.ParentProcessId
        if (-not $parentPid -or $parentPid -eq 0 -or $parentPid -eq $currentPid) { break }
        $parentFilter = "ProcessId = $parentPid"
        $parentProc = Get-CimInstance -ClassName Win32_Process -Filter $parentFilter -ErrorAction SilentlyContinue
        if (-not $parentProc) { break }
        if ($parentProc.Name -match "(?i)^agy(\.exe)?$") {
            return $true
        }
        $currentPid = $parentPid
        $hop++
    }
    return $false
}

if (Test-InsideAgy) {
    Write-Host "Run this in a normal terminal, not inside agy." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Pre-flight: Check installed mswap and require >= 2 spare accounts
# ---------------------------------------------------------------------------
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " mswap Live-Switch Spike (U1, U2, U6, U7)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Running pre-flight checks via 'mswap list --json'..."

$listRaw = & mswap list --json
if ($LASTEXITCODE -ne 0 -or -not $listRaw) {
    Write-Host "Error: Could not retrieve accounts from 'mswap list --json'." -ForegroundColor Red
    exit 1
}

try {
    $listJson = $listRaw | ConvertFrom-Json
} catch {
    Write-Host "Error: Failed to parse JSON from 'mswap list --json'." -ForegroundColor Red
    exit 1
}

$accounts = $listJson.data.accounts
$accountCount = if ($accounts) { ($accounts | Measure-Object).Count } else { 0 }
if ($accountCount -lt 2) {
    Write-Host ("Error: At least 2 saved accounts are required for this spike (found {0})." -f $accountCount) -ForegroundColor Red
    Write-Host "Please add at least 2 spare accounts using 'mswap add' before running this spike." -ForegroundColor Yellow
    exit 1
}

# Designate first two accounts as Account A and Account B
$accountA = $accounts[0]
$accountB = $accounts[1]

Write-Host ""
Write-Host "Detected accounts for testing:"
Write-Host ("  Account A: Slot {0} ({1})" -f $accountA.slot, $accountA.email)
Write-Host ("  Account B: Slot {0} ({1})" -f $accountB.slot, $accountB.email)
Write-Host ""
Write-Host "CRITICAL SAFETY CHECK: Never run live switch spikes on your primary Google account." -ForegroundColor Yellow
$confirm = Read-Host "Type YES to confirm that BOTH accounts are SPARE accounts (type YES)"
if ($confirm -ne "YES") {
    Write-Host "Confirmation not received ('YES'). Aborting for safety." -ForegroundColor Yellow
    exit 1
}

# Redaction helper to ensure real emails are never logged to findings
function Redact-Text {
    param([string]$InputText)
    if (-not $InputText) { return "" }
    $res = $InputText
    foreach ($acc in $accounts) {
        if ($acc.email) {
            $aliasName = if ($acc.slot -eq $accountA.slot) { "account A" } elseif ($acc.slot -eq $accountB.slot) { "account B" } else { "account " + $acc.slot }
            $res = $res -ireplace [regex]::Escape($acc.email), $aliasName
        }
    }
    # Redact any other email address pattern
    $res = [regex]::Replace($res, "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "user@example.com")
    return $res
}

# ---------------------------------------------------------------------------
# Stage 1: Stage U6 - Clean sign-in after live entry is deleted
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host " Stage 1 / Stage U6: Clean Sign-in After Live Entry Deleted" -ForegroundColor Cyan
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Objective: Determine if removing the live credential causes agy to"
Write-Host "launch a clean sign-in browser flow (required for 'add --new')."
Write-Host ""
Write-Host "Instructions:"
Write-Host "  1. In a separate terminal, run:"
Write-Host "       mswap add --new" -ForegroundColor Yellow
Write-Host "  2. Then in that terminal, run:"
Write-Host "       agy" -ForegroundColor Yellow
Write-Host "  3. Observe whether agy prompts with a normal Google sign-in flow."
Write-Host "  4. Complete or cancel sign-in as appropriate."
Write-Host ""
[void](Read-Host "Press Enter once you have run 'mswap add --new' and 'agy'...")

$u6Answer = Read-Host "Did agy show a normal sign-in flow? [y/n]"
Write-Host ("Recorded U6 answer: {0}" -f $u6Answer) -ForegroundColor Green
Write-Host ""
[void](Read-Host "Press Enter to continue to Stage 2...")

# ---------------------------------------------------------------------------
# Stage 2: Stage U1 / U2 - Running agy session switch & write-back
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host " Stage 2 / Stage U1 & U2: Live Session Switch & Write-Back" -ForegroundColor Cyan
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Objective:"
Write-Host "  - U1: Does a running agy session pick up a credential switch?"
Write-Host "  - U2: Does a running agy write back the old token at refresh (~60m)?"
Write-Host ""
Write-Host "Instructions:"
Write-Host "  1. Open a SECOND terminal window."
Write-Host "  2. Start agy in that second terminal:"
Write-Host "       agy" -ForegroundColor Yellow
Write-Host "  3. In agy, send the message:"
Write-Host "       say ok" -ForegroundColor Yellow
Write-Host ""
[void](Read-Host "Press Enter once agy responded in the second terminal...")

# Capture status before switch
$beforeCurrentRaw = & mswap current --json
$beforeCurrent = try { $beforeCurrentRaw | ConvertFrom-Json } catch { $null }
$beforeSlot = if ($beforeCurrent -and $beforeCurrent.data.active) { $beforeCurrent.data.active.slot } else { "unknown" }
$beforeFp = if ($beforeCurrent -and $beforeCurrent.data.active) { $beforeCurrent.data.active.fp } else { "unknown" }

Write-Host ""
Write-Host "Switching active account via 'mswap switch'..." -ForegroundColor Green
& mswap switch
Write-Host ""
Write-Host "Active account reported by 'mswap current':"
& mswap current

$switchedCurrentRaw = & mswap current --json
$switchedCurrent = try { $switchedCurrentRaw | ConvertFrom-Json } catch { $null }
$switchedSlot = if ($switchedCurrent -and $switchedCurrent.data.active) { $switchedCurrent.data.active.slot } else { "unknown" }
$switchedFp = if ($switchedCurrent -and $switchedCurrent.data.active) { $switchedCurrent.data.active.fp } else { "unknown" }

Write-Host ""
Write-Host "Instructions:"
Write-Host "  In the SECOND terminal where agy is still running, send the message:"
Write-Host "       say ok again" -ForegroundColor Yellow
Write-Host ""
[void](Read-Host "Press Enter once you sent 'say ok again' in the running agy...")

Write-Host ""
Write-Host "Waiting up to 70 minutes to observe whether agy writes back the old account at token refresh..."
Write-Host "(Google access tokens expire in 60 minutes; agy will refresh before or at expiry)."
Write-Host "You can press [Enter] at any time to finish the wait early."
Write-Host ""

$totalWaitSeconds = 70 * 60
$elapsed = 0
$waitEndedEarly = $false

while ($elapsed -lt $totalWaitSeconds) {
    $remaining = $totalWaitSeconds - $elapsed
    $m = [math]::Floor($remaining / 60)
    $s = $remaining % 60
    Write-Host ("`r[Countdown: {0:D2}m {1:D2}s remaining] (Press Enter to finish wait early)... " -f $m, $s) -NoNewline

    try {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq [ConsoleKey]::Enter) {
                $waitEndedEarly = $true
                Write-Host "`nWait ended early by user."
                break
            }
        }
    } catch {
        # Fallback if Console input is not available
    }

    Start-Sleep -Seconds 1
    $elapsed++
}

if (-not $waitEndedEarly) {
    Write-Host "`n70-minute wait completed."
}

# Check active account after wait
$afterCurrentRaw = & mswap current --json
$afterCurrent = try { $afterCurrentRaw | ConvertFrom-Json } catch { $null }
$afterSlot = if ($afterCurrent -and $afterCurrent.data.active) { $afterCurrent.data.active.slot } else { "unknown" }
$afterFp = if ($afterCurrent -and $afterCurrent.data.active) { $afterCurrent.data.active.fp } else { "unknown" }

$writeBackDetected = ($switchedFp -ne "unknown" -and $beforeFp -ne "unknown" -and $afterFp -eq $beforeFp -and $afterFp -ne $switchedFp)

Write-Host ""
Write-Host "Credential state summary:"
Write-Host ("  Before switch: Slot {0} (fp {1})" -f $beforeSlot, $beforeFp)
Write-Host ("  After switch:  Slot {0} (fp {1})" -f $switchedSlot, $switchedFp)
Write-Host ("  After wait:    Slot {0} (fp {1})" -f $afterSlot, $afterFp)
if ($writeBackDetected) {
    Write-Host "  -> WRITE-BACK OBSERVED: agy restored the old account!" -ForegroundColor Magenta
} else {
    Write-Host "  -> No write-back observed: Active account remained switched." -ForegroundColor Green
}

Write-Host ""
$u1KeepWorking = Read-Host "After the switch, did the running agy keep working? [y/n]"
Write-Host ""
Write-Host "Check quota usage by running in another terminal: mswap list --refresh"
$u1QuotaAnswer = Read-Host "Which account's quota dropped? (check 'mswap list --refresh')"
Write-Host ""
[void](Read-Host "Press Enter to continue to Stage 3...")

# ---------------------------------------------------------------------------
# Stage 3: Stage U7 (OPTIONAL) - agy /logout Revocation Check
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host " Stage 3 / Stage U7: agy /logout Revocation Check (OPTIONAL)" -ForegroundColor Cyan
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "WARNING: This stage tests whether agy's /logout revokes the refresh"
Write-Host "token with Google. Running this will permanently invalidate one"
Write-Host "spare login's saved copy. You will need to re-authenticate it later."
Write-Host "This test is SKIPPED by default."
Write-Host ""
$u7Input = Read-Host "Type 'REVOKE-TEST' to run this test, or press Enter to skip"

$u7ResultText = "Skipped by user"
if ($u7Input -eq "REVOKE-TEST") {
    Write-Host ""
    Write-Host "Stage U7 Instructions:"
    Write-Host "  1. In the running agy session on a SPARE account, type:"
    Write-Host "       /logout" -ForegroundColor Yellow
    Write-Host "  2. Once agy completes logout, press Enter here."
    [void](Read-Host "Press Enter after agy /logout completes...")

    Write-Host ""
    Write-Host "Running 'mswap doctor --online' to check for quarantined credentials..."
    & mswap doctor --online
    $docCode = $LASTEXITCODE

    Write-Host ""
    $u7QuarantineObserved = Read-Host "Did mswap doctor show the slot as quarantined? [y/n]"
    $u7ResultText = ("Ran test. Observed quarantined: {0} (doctor exit code: {1})" -f $u7QuarantineObserved, $docCode)
} else {
    Write-Host "Stage U7 skipped."
}

# ---------------------------------------------------------------------------
# Record Findings
# ---------------------------------------------------------------------------
$nowIso = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
$findingsRelPath = Join-Path $PSScriptRoot "..\..\.agent\findings\W2-live-switch.md"
$findingsPath = [System.IO.Path]::GetFullPath($findingsRelPath)

$resultsBlock = @"

## Results · $nowIso
- Timestamp: $nowIso
- Wait duration: $elapsed seconds (Early exit: $waitEndedEarly)
- Test accounts:
  - Account A: slot $($accountA.slot)
  - Account B: slot $($accountB.slot)
- U6 (Clean sign-in flow after live deleted): $u6Answer
- U1 (Running agy kept working after switch): $u1KeepWorking
- U1 (Quota attribution from mswap list --refresh): $(Redact-Text $u1QuotaAnswer)
- U2 (Write-back observed): $writeBackDetected
  - Details: before=slot $beforeSlot (fp $beforeFp), switched=slot $switchedSlot (fp $switchedFp), after=slot $afterSlot (fp $afterFp)
- U7 (/logout revocation check): $(Redact-Text $u7ResultText)
"@

$findingsDir = Split-Path $findingsPath
if (-not (Test-Path $findingsDir)) {
    New-Item -ItemType Directory -Path $findingsDir -Force | Out-Null
}

Add-Content -Path $findingsPath -Value $resultsBlock -Encoding UTF8

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Spike completed! Results appended to:" -ForegroundColor Green
Write-Host "   $findingsPath"
Write-Host "=================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Please notify agy by stating: 'W2.S1 findings are in'" -ForegroundColor Cyan
