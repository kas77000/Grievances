<#
Creates a desktop icon that opens the Order Alert form in its own Edge window (no address bar).

    powershell -ExecutionPolicy Bypass -File install-shortcut.ps1 -HtmlPath '\\fileserver\share\OrderAlerts\app\order-alert.html'

Keep order-alert.html on the share so every user gets updates without reinstalling.
#>
param(
    [Parameter(Mandatory = $true)][string]$HtmlPath,
    [string]$Name = "Order Alert",
    [string]$IconPath = ""
)

$edge = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge not found." }

# file:///C:/... for local paths, file://server/share/... for UNC paths
$url = ([System.Uri]$HtmlPath).AbsoluteUri

$lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "$Name.lnk"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($lnkPath)
$lnk.TargetPath = $edge
$lnk.Arguments = "--app=`"$url`""
$lnk.Description = "Report an order execution problem to Quant & Support"
$lnk.IconLocation = $(if ($IconPath) { $IconPath } else { "$env:SystemRoot\System32\shell32.dll,77" })
$lnk.Save()
Write-Host "Created $lnkPath -> $url"
