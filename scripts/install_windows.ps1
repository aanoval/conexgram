$ErrorActionPreference = "Stop"

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $Python) {
  $Python = Get-Command python3 -ErrorAction SilentlyContinue
}
if (-not $Python) {
  throw "Python was not found in PATH."
}
$StateDir = Join-Path $HOME ".conexgram"
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
$ConfigPath = Join-Path $StateDir "config.json"

if (-not (Test-Path $ConfigPath)) {
  & $Python.Source -m conexgram --config $ConfigPath setup
}

& $Python.Source -m conexgram --config $ConfigPath doctor --fix

$Action = New-ScheduledTaskAction `
  -Execute $Python.Source `
  -Argument "-m conexgram --config `"$ConfigPath`" run" `
  -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName "Conexgram" `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Conexgram Telegram connector for Codex CLI" `
  -Force | Out-Null

Start-ScheduledTask -TaskName "Conexgram"

Write-Host "Installed and started Windows Scheduled Task: Conexgram"
Write-Host "Status: Get-ScheduledTask -TaskName Conexgram"
Write-Host "Stop:   Stop-ScheduledTask -TaskName Conexgram"
