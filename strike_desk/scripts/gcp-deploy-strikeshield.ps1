# Deploy StrikeShield + overlay strike payload onto the live GCE iter-07 tree.
# Does not flip Analyze. Restarts strike-desk only. Never prints the Hands token.
param(
    [string]$Project = "ztartcopenalgo",
    [string]$Zone = "asia-south1-a",
    [string]$Instance = "strike-desk"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $Root "strike_desk\src\strike_desk\strikeshield.py"))) {
    $Root = Split-Path $PSScriptRoot -Parent
}
$Src = Join-Path $Root "strike_desk\src\strike_desk"
$HostSrc = Join-Path $Root "strike_desk\.state-manual\_host_src"
$EnvFile = Join-Path $Root "strike_desk\.env"
$Remote = "${Instance}:/tmp/sd-strikeshield"

function Invoke-GCloudSsh([string]$Command) {
    & gcloud compute ssh $Instance --zone=$Zone --project=$Project --quiet --command=$Command
    if ($LASTEXITCODE -ne 0) { throw "gcloud ssh failed: $Command" }
}

function Copy-ToHost([string]$Local) {
    & gcloud compute scp $Local $Remote --zone=$Zone --project=$Project --quiet
    if ($LASTEXITCODE -ne 0) { throw "gcloud scp failed: $Local" }
}

if (-not (Test-Path $EnvFile)) { throw "missing $EnvFile" }
$TokenLine = Select-String -Path $EnvFile -Pattern '^STRIKE_DESK_TT_AUTH_TOKEN_HANDS=(\S+)' |
    Select-Object -First 1
if (-not $TokenLine) {
    $TokenLine = Select-String -Path $EnvFile -Pattern '^STRIKE_DESK_TT_AUTH_TOKEN=(\S+)' |
        Where-Object { $_.Line -notmatch 'OVERLAY|_BN|_HANDS' } |
        Select-Object -First 1
}
if (-not $TokenLine) { throw "STRIKE_DESK_TT_AUTH_TOKEN_HANDS missing from local .env" }
$Token = $TokenLine.Line.Split("=", 2)[1].Trim()

$TokenFile = Join-Path $env:TEMP "sd-tt.token"
Set-Content -Path $TokenFile -Value $Token -NoNewline

Invoke-GCloudSsh "sudo mkdir -p /tmp/sd-strikeshield && sudo chown `$USER:`$USER /tmp/sd-strikeshield"
@(
    (Join-Path $Src "strikeshield.py"),
    (Join-Path $Src "tradetron_hands.py"),
    (Join-Path $HostSrc "config.py"),
    (Join-Path $HostSrc "service.py"),
    (Join-Path $HostSrc "__main__.py"),
    (Join-Path $HostSrc "tradetron_signal.py"),
    (Join-Path $HostSrc "openalgo_client.py"),
    (Join-Path $HostSrc "watchdog.sh"),
    (Join-Path $HostSrc "install.sh")
) | ForEach-Object { Copy-ToHost $_ }
Copy-ToHost $TokenFile
Remove-Item $TokenFile -Force
Invoke-GCloudSsh "mv /tmp/sd-strikeshield/sd-tt.token /tmp/sd-strikeshield/tt.token && sed -i 's/\r`$//' /tmp/sd-strikeshield/install.sh /tmp/sd-strikeshield/watchdog.sh && sudo bash /tmp/sd-strikeshield/install.sh"
Write-Host "DEPLOY_OK"
