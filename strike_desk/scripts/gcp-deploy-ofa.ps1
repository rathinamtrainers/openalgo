# Deploy OFA as an always-on systemd unit onto the live GCE iter-07 tree.
# Does not flip Analyze. Restarts strike-desk-ofa + overlays stack lamp. Never prints the OFA token.
param(
    [string]$Project = "ztartcopenalgo",
    [string]$Zone = "asia-south1-a",
    [string]$Instance = "strike-desk"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $Root "strike_desk\src\strike_desk\ofa_live.py"))) {
    $Root = Split-Path $PSScriptRoot -Parent
}
$Src = Join-Path $Root "strike_desk\src\strike_desk"
$HostSrc = Join-Path $Root "strike_desk\.state-manual\_host_src"
$EnvFile = Join-Path $Root "strike_desk\.env"
$StackSvc = Join-Path $Root "services\stack_status_service.py"
$Remote = "${Instance}:/tmp/sd-ofa"

function Invoke-GCloudSsh([string]$Command) {
    & gcloud compute ssh $Instance --zone=$Zone --project=$Project --quiet --command=$Command
    if ($LASTEXITCODE -ne 0) { throw "gcloud ssh failed: $Command" }
}

function Copy-ToHost([string]$Local) {
    & gcloud compute scp $Local $Remote --zone=$Zone --project=$Project --quiet
    if ($LASTEXITCODE -ne 0) { throw "gcloud scp failed: $Local" }
}

if (-not (Test-Path $EnvFile)) { throw "missing $EnvFile" }
if (-not (Test-Path $StackSvc)) { throw "missing $StackSvc" }
$TokenLine = Select-String -Path $EnvFile -Pattern '^STRIKE_DESK_TT_AUTH_TOKEN_OFA=(\S+)' |
    Select-Object -First 1
if (-not $TokenLine) { throw "STRIKE_DESK_TT_AUTH_TOKEN_OFA missing from local .env" }
$Token = $TokenLine.Line.Split("=", 2)[1].Trim()

$TokenFile = Join-Path $env:TEMP "sd-tt-ofa.token"
Set-Content -Path $TokenFile -Value $Token -NoNewline

Invoke-GCloudSsh "sudo mkdir -p /tmp/sd-ofa && sudo chown `$USER:`$USER /tmp/sd-ofa && rm -f /tmp/sd-ofa/*"
@(
    (Join-Path $Src "ofa.py"),
    (Join-Path $Src "ofa_live.py"),
    (Join-Path $Src "ofa_analyzer.py"),
    (Join-Path $Src "ofa_hands.py"),
    (Join-Path $Src "ofa_main.py"),
    (Join-Path $HostSrc "config.py"),
    (Join-Path $HostSrc "openalgo_client.py"),
    (Join-Path $HostSrc "watchdog.sh"),
    (Join-Path $HostSrc "install-ofa.sh"),
    (Join-Path $HostSrc "strike-desk-ofa.service"),
    $StackSvc
) | ForEach-Object {
    if (-not (Test-Path $_)) { throw "missing $_" }
    Copy-ToHost $_
}
Copy-ToHost $TokenFile
Remove-Item $TokenFile -Force
Invoke-GCloudSsh "mv /tmp/sd-ofa/sd-tt-ofa.token /tmp/sd-ofa/tt-ofa.token && sed -i 's/\r`$//' /tmp/sd-ofa/install-ofa.sh /tmp/sd-ofa/watchdog.sh && sudo install -m 0644 /tmp/sd-ofa/stack_status_service.py /opt/openalgo/current/services/stack_status_service.py && sudo chown openalgo:openalgo /opt/openalgo/current/services/stack_status_service.py && sudo bash /tmp/sd-ofa/install-ofa.sh && sudo systemctl restart openalgo.service && sleep 3 && systemctl is-active openalgo.service && systemctl is-active strike-desk-ofa.service && cat /var/tmp/strike-desk-stack.json"
Write-Host "DEPLOY_OK"
