<#
Installs the Order Alert desktop notifier for the current user: a scheduled task that starts it,
hidden, at every logon (no admin rights needed). Run once per team member:

    powershell -ExecutionPolicy Bypass -File \\fileserver\share\OrderAlerts\notifier\install-notifier.ps1 `
        -ProcessedPath '\\fileserver\share\OrderAlerts\processed' -DeskUrl 'http://order-desk-host:8501'

    -MinSeverity high          only be told about high/critical issues
    -RemindUnassignedMinutes 0 no "still unassigned" reminders
    -Uninstall                 remove it
#>
param(
    [string]$ProcessedPath,
    [string]$DeskUrl = "",
    [ValidateSet("low", "medium", "high", "critical")][string]$MinSeverity = "low",
    [int]$RemindUnassignedMinutes = 10,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$TaskName = "Order Alert notifier"

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -Path "HKCU:\Software\Classes\AppUserModelId\OrderAlert.Desk.Notifier" -Recurse -ErrorAction SilentlyContinue
    Write-Host "Removed '$TaskName'."
    exit 0
}
if (-not $ProcessedPath) { throw "-ProcessedPath is required (the watchdog's processed folder)." }
if (-not (Test-Path $ProcessedPath)) { Write-Warning "$ProcessedPath is not reachable right now; installing anyway." }

# Run the copy on the share, so updating it there updates everyone at next logon.
$script = Join-Path $PSScriptRoot "order-alert-notifier.ps1"
$taskArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -ProcessedPath `"$ProcessedPath`" " +
        "-MinSeverity $MinSeverity -RemindUnassignedMinutes $RemindUnassignedMinutes"
if ($DeskUrl) { $taskArgs += " -DeskUrl `"$DeskUrl`"" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Windows notifications for new Order Alert issues" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

& powershell -NoProfile -ExecutionPolicy Bypass -File $script -ProcessedPath $ProcessedPath -DeskUrl $DeskUrl -Test
Write-Host "Installed '$TaskName'. A sample notification was shown; real ones follow as issues arrive."
