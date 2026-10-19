<#
.SYNOPSIS
  Copies private files from this PC to the NDHD server over SSH (scp):
  .env, the Robinhood snapshot, or the engine's state file. Never prints
  their contents.

.DESCRIPTION
  These files are git-ignored, so `git pull` on the server never brings
  them. Uses Windows' built-in OpenSSH and the private key Oracle gave you
  when you created the VM. -Server can be the VM's public IP or, once it
  has joined your tailnet, its Tailscale name (ndhd-server).

  -State copies the engine's state file (positions it manages, Wheel phases,
  kill switch date). Copy it only while BOTH engines are stopped; the
  switch-to-server.ps1 script does this for you in the right order.

.EXAMPLE
  .\scripts\sync-to-server.ps1 -Server 129.146.1.2 -Key $HOME\.ssh\oracle-ndhd.key -DotEnv
.EXAMPLE
  .\scripts\sync-to-server.ps1 -Server ndhd-server -Key $HOME\.ssh\oracle-ndhd.key -Snapshot
#>
param(
    [Parameter(Mandatory)] [string]$Server,
    [Parameter(Mandatory)] [string]$Key,
    [string]$User = 'ubuntu',
    [string]$RemoteDir = 'NDHD-Trading',
    [switch]$DotEnv,
    [switch]$Snapshot,
    [switch]$State
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not ($DotEnv -or $Snapshot -or $State)) { throw "Nothing to copy: pass -DotEnv, -Snapshot and/or -State." }
if (-not (Test-Path $Key)) { throw "SSH key not found: $Key" }

$sshOpts = @('-i', $Key, '-o', 'StrictHostKeyChecking=accept-new', '-o', 'BatchMode=yes')
$target = "$User@$Server"

function Copy-One($localRel, $remoteRel, [switch]$Private) {
    $local = Join-Path $root $localRel
    if (-not (Test-Path $local)) { throw "Not found on this PC: $localRel" }
    & ssh @sshOpts $target "mkdir -p '$RemoteDir/$(Split-Path $remoteRel -Parent)'" | Out-Null
    & scp @sshOpts -q $local "${target}:$RemoteDir/$remoteRel"
    if ($LASTEXITCODE -ne 0) { throw "scp failed for $localRel (exit $LASTEXITCODE)." }
    if ($Private) { & ssh @sshOpts $target "chmod 600 '$RemoteDir/$remoteRel'" | Out-Null }
    Write-Host "Copied $localRel -> ${Server}:$RemoteDir/$remoteRel" -ForegroundColor Green
}

if ($DotEnv) { Copy-One '.env' '.env' -Private }
if ($Snapshot) { Copy-One 'data\robinhood_snapshot.json' 'data/robinhood_snapshot.json' -Private }
if ($State) {
    $stateFile = (& (Join-Path $root '.venv\Scripts\python.exe') -c "from dotenv import dotenv_values; print(dotenv_values(r'$root\.env').get('STATE_FILE') or 'portfolio_engine_state.json')").Trim()
    Copy-One $stateFile $stateFile -Private
}
