# Overlay the cash-index Historify 1m archive timer. Does not restart CoA.
param(
    [string]$Project = "ztartcopenalgo",
    [string]$Zone = "asia-south1-a",
    [string]$Instance = "strike-desk"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $Root "strike_desk\src\strike_desk\coa_archive.py"))) {
    $Root = Split-Path $PSScriptRoot -Parent
}
$Src = Join-Path $Root "strike_desk\src\strike_desk"
$Scripts = Join-Path $Root "strike_desk\scripts"
$Remote = "${Instance}:/tmp/sd-coa-archive"

function Invoke-GCloudSsh([string]$Command) {
    & gcloud compute ssh $Instance --zone=$Zone --project=$Project --quiet --command=$Command
    if ($LASTEXITCODE -ne 0) { throw "gcloud ssh failed: $Command" }
}

function Copy-ToHost([string]$Local) {
    & gcloud compute scp $Local $Remote --zone=$Zone --project=$Project --quiet
    if ($LASTEXITCODE -ne 0) { throw "gcloud scp failed: $Local" }
}

Invoke-GCloudSsh "sudo mkdir -p /tmp/sd-coa-archive && sudo chown `$USER:`$USER /tmp/sd-coa-archive && rm -f /tmp/sd-coa-archive/*"
@(
    (Join-Path $Src "coa_archive.py"),
    (Join-Path $Src "coa_boards.py"),
    (Join-Path $Scripts "coa_historify_archive.py"),
    (Join-Path $Scripts "coa_replay_host.py"),
    (Join-Path $Scripts "install-coa-historify-archive.sh"),
    (Join-Path $Scripts "coa-historify-archive.service"),
    (Join-Path $Scripts "coa-historify-archive.timer")
) | ForEach-Object {
    if (-not (Test-Path $_)) { throw "missing $_" }
    Copy-ToHost $_
}
Invoke-GCloudSsh "sed -i 's/\r`$//' /tmp/sd-coa-archive/install-coa-historify-archive.sh && sudo bash /tmp/sd-coa-archive/install-coa-historify-archive.sh && grep -n CASH_BOARDS /opt/strike-desk/current/strike_desk/src/strike_desk/coa_boards.py && grep -n archive_board /opt/strike-desk/current/strike_desk/scripts/coa_historify_archive.py"
Write-Host "ARCHIVE_DEPLOY_OK"
