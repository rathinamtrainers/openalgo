# Deploy CoA live as its own systemd unit. Does not restart strike-desk.service.
# Never prints the CoA Hands token.
param(
    [string]$Project = "ztartcopenalgo",
    [string]$Zone = "asia-south1-a",
    [string]$Instance = "strike-desk"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $Root "strike_desk\src\strike_desk\ltp_coa_live.py"))) {
    $Root = Split-Path $PSScriptRoot -Parent
}
$Src = Join-Path $Root "strike_desk\src\strike_desk"
$Scripts = Join-Path $Root "strike_desk\scripts"
$EnvFile = Join-Path $Root "strike_desk\.env"
$Remote = "${Instance}:/tmp/sd-coa"
$OaBooks = Join-Path $Root "database\strategy_books_db.py"
$OaBooksSvc = Join-Path $Root "services\strategy_books_service.py"
$OaTimeline = Join-Path $Root "services\strategy_timeline_service.py"
$OaPerf = Join-Path $Root "services\strategy_book_performance.py"

function Invoke-GCloudSsh([string]$Command) {
    & gcloud compute ssh $Instance --zone=$Zone --project=$Project --quiet --command=$Command
    if ($LASTEXITCODE -ne 0) { throw "gcloud ssh failed: $Command" }
}

function Copy-ToHost([string]$Local) {
    & gcloud compute scp $Local $Remote --zone=$Zone --project=$Project --quiet
    if ($LASTEXITCODE -ne 0) { throw "gcloud scp failed: $Local" }
}

if (-not (Test-Path $EnvFile)) { throw "missing $EnvFile" }
$TokenLine = Select-String -Path $EnvFile -Pattern '^STRIKE_DESK_TT_AUTH_TOKEN_COA=(\S+)' |
    Select-Object -First 1
if (-not $TokenLine) { throw "STRIKE_DESK_TT_AUTH_TOKEN_COA missing from local .env" }
$Token = $TokenLine.Line.Split("=", 2)[1].Trim()

$TokenFile = Join-Path $env:TEMP "sd-tt-coa.token"
Set-Content -Path $TokenFile -Value $Token -NoNewline

Invoke-GCloudSsh "sudo mkdir -p /tmp/sd-coa && sudo chown `$USER:`$USER /tmp/sd-coa && rm -f /tmp/sd-coa/*"
@(
    (Join-Path $Src "ltp_coa.py"),
    (Join-Path $Src "ltp_coa_exits.py"),
    (Join-Path $Src "ltp_coa_brief.py"),
    (Join-Path $Src "ltp_coa_replay.py"),
    (Join-Path $Src "ltp_coa_live.py"),
    (Join-Path $Src "ltp_coa_intel.py"),
    (Join-Path $Src "ltp_coa_narrate.py"),
    (Join-Path $Src "tradetron_hands.py"),
    (Join-Path $Src "coa_analyzer.py"),
    (Join-Path $Src "ofa_analyzer.py"),
    (Join-Path $Src "coa_main.py"),
    (Join-Path $Src "coa_boards.py"),
    (Join-Path $Src "openalgo_client.py"),
    (Join-Path $Src "delta_india.py"),
    (Join-Path $Src "config.py"),
    (Join-Path $Src "__main__.py"),
    (Join-Path $Scripts "desk.zerotouchalgo.com.nginx.conf"),
    (Join-Path $Scripts "install-coa.sh"),
    (Join-Path $Scripts "strike-desk-coa.service"),
    $OaBooks,
    $OaBooksSvc,
    $OaTimeline,
    $OaPerf
) | ForEach-Object {
    if (-not (Test-Path $_)) { throw "missing $_" }
    Copy-ToHost $_
}
Copy-ToHost $TokenFile
Remove-Item $TokenFile -Force
$BtcLine = Select-String -Path $EnvFile -Pattern '^STRIKE_DESK_TT_AUTH_TOKEN_COA_BTC=(\S+)' |
    Select-Object -First 1
if ($BtcLine) {
    $BtcToken = $BtcLine.Line.Split("=", 2)[1].Trim()
    if ($BtcToken) {
        $BtcFile = Join-Path $env:TEMP "sd-tt-coa-btc.token"
        Set-Content -Path $BtcFile -Value $BtcToken -NoNewline
        Copy-ToHost $BtcFile
        Remove-Item $BtcFile -Force
        Invoke-GCloudSsh "mv /tmp/sd-coa/sd-tt-coa-btc.token /tmp/sd-coa/tt-coa-btc.token"
    }
}
Invoke-GCloudSsh "mv /tmp/sd-coa/sd-tt-coa.token /tmp/sd-coa/tt-coa.token && sed -i 's/\r`$//' /tmp/sd-coa/install-coa.sh && sudo bash /tmp/sd-coa/install-coa.sh && systemctl is-active strike-desk-coa.service && systemctl is-active strike-desk.service && python3 -c 'import pathlib; t=pathlib.Path(\"/opt/strike-desk/current/strike_desk/src/strike_desk/coa_main.py\").read_text(); assert \"make_btc_runner\" in t; assert \"DeltaIndiaChain\" in t'"
Write-Host "DEPLOY_OK"
