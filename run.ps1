# Windows helper — same contract as run.sh
param(
  [Parameter(Mandatory = $true)][string]$SimUrl
)
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$hash = [Math]::Abs($SimUrl.GetHashCode())
$DataDir = if ($env:CONTROLLER_DATA) { $env:CONTROLLER_DATA } else { Join-Path $Root "controller_data\run_$hash" }
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$env:CONTROLLER_DATA = $DataDir
$env:PYTHONPATH = "$Root;$env:PYTHONPATH"
Write-Host "controller starting against $SimUrl (data=$DataDir)"
python -m controller run --url $SimUrl --data-dir $DataDir
