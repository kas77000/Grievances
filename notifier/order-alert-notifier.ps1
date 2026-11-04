<#
Order Alert desktop notifier: pops a Windows notification when a new issue lands on the share,
and reminds the team when an urgent issue is still unassigned. Clicking it opens the issue in
the Order Alert Desk. Windows PowerShell 5.1 only, nothing to install.

    powershell -NoProfile -ExecutionPolicy Bypass -File order-alert-notifier.ps1 `
        -ProcessedPath '\\fileserver\share\OrderAlerts\processed' -DeskUrl 'http://order-desk-host:8501'

    -Test     show one sample notification and exit
    -Once     one pass and exit (for testing)
    -DryRun   print notifications as JSON lines instead of showing them (for testing)

Usually started at logon by install-notifier.ps1.
#>
param(
    [Parameter(Mandatory = $true)][string]$ProcessedPath,
    [string]$DeskUrl = "",
    [ValidateSet("low", "medium", "high", "critical")][string]$MinSeverity = "low",
    [int]$RemindUnassignedMinutes = 10,       # 0 = no reminders
    [ValidateSet("low", "medium", "high", "critical")][string]$RemindMinSeverity = "high",
    [int]$PollSeconds = 20,
    [int]$CatchUpMinutes = 60,                # on first start, only announce issues this recent
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA "OrderAlert"),
    [switch]$Once,
    [switch]$DryRun,
    [switch]$Test
)

$ErrorActionPreference = "Stop"
$Rank = @{ low = 0; medium = 1; high = 2; critical = 3 }
# Our own app identity, so notifications say "Order Alert" with our logo instead of PowerShell.
$AppId = "OrderAlert.Desk.Notifier"
$AppName = "Order Alert"

New-Item -ItemType Directory -Force $StateDir | Out-Null
$StateFile = Join-Path $StateDir "notifier-state.json"
$LogFile = Join-Path $StateDir "notifier.log"
$AssetDir = Join-Path $StateDir "assets"

# Register the app id for this user (HKCU, no admin) and keep local copies of the icons:
# Windows won't load notification images from a network share. Idempotent, runs at every start.
function Register-App {
    $src = Join-Path $PSScriptRoot "assets"
    New-Item -ItemType Directory -Force $AssetDir | Out-Null
    if (Test-Path $src) { Copy-Item -Path (Join-Path $src "*.png") -Destination $AssetDir -Force }
    $key = "HKCU:\Software\Classes\AppUserModelId\$AppId"
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name DisplayName -Value $AppName
    Set-ItemProperty -Path $key -Name IconUri -Value (Join-Path $AssetDir "order-alert.png")
    Set-ItemProperty -Path $key -Name IconBackgroundColor -Value "FF0D1016"
    Set-ItemProperty -Path $key -Name ShowInSettings -Value 1 -Type DWord   # listed in Settings > Notifications
}

function Write-Log([string]$msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Esc([string]$s) { [System.Security.SecurityElement]::Escape([string]$s) }

function Show-Toast([string]$kind, [string]$severity, [string]$title, [string]$line2, [string]$issueId) {
    $url = if ($DeskUrl) { "$($DeskUrl.TrimEnd('/'))/issue?id=$([uri]::EscapeDataString($issueId))" } else { "" }
    if ($DryRun) {
        [pscustomobject]@{ kind = $kind; severity = $severity; title = $title; detail = $line2; id = $issueId; url = $url } |
            ConvertTo-Json -Compress | Write-Output
        return
    }
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $head = if ($kind -eq "reminder") { "Still unassigned - $($severity.ToUpper())" } else { "New order issue - $($severity.ToUpper())" }
    $badge = Join-Path $AssetDir "sev-$severity.png"
    $logo = if (Test-Path $badge) { "<image placement=`"appLogoOverride`" hint-crop=`"circle`" src=`"$(Esc ([uri]$badge).AbsoluteUri)`"/>" } else { "" }
    $launch = if ($url) { " activationType=`"protocol`" launch=`"$(Esc $url)`"" } else { "" }
    # Urgent issues stay on screen until dismissed instead of fading after a few seconds.
    $scenario = if ($Rank[$severity] -ge $Rank["high"]) { " scenario=`"reminder`"" } else { "" }
    $actions = if ($url) { "<actions><action content=`"Open in Desk`" activationType=`"protocol`" arguments=`"$(Esc $url)`"/><action content=`"Dismiss`" arguments=`"dismiss`" activationType=`"system`"/></actions>" } else { "" }
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml("<toast$launch$scenario><visual><binding template=`"ToastGeneric`"><text>$(Esc $head)</text><text>$(Esc $title)</text><text>$(Esc $line2)</text>$logo<text placement=`"attribution`">Order Alert Desk</text></binding></visual>$actions</toast>")
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($AppId)
    $setting = "$($notifier.Setting)"
    if ($setting -and $setting -ne "Enabled") { Write-Log "notifications are $setting for $AppName (Windows settings / Do not disturb)" }
    $notifier.Show([Windows.UI.Notifications.ToastNotification]::new($xml))
}

function Load-State {
    if (Test-Path $StateFile) {
        try {
            $s = Get-Content $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
            return @{ seen = [System.Collections.Generic.HashSet[string]]@($s.seen); reminded = [System.Collections.Generic.HashSet[string]]@($s.reminded); fresh = $false }
        } catch { Write-Log "State file unreadable, starting fresh: $_" }
    }
    return @{ seen = [System.Collections.Generic.HashSet[string]]::new(); reminded = [System.Collections.Generic.HashSet[string]]::new(); fresh = $true }
}

function Save-State($state) {
    # Keep the file small: ids are date-prefixed and only recent folders are scanned anyway.
    $seen = @($state.seen | Sort-Object -Descending | Select-Object -First 2000)
    $reminded = @($state.reminded | Sort-Object -Descending | Select-Object -First 2000)
    $tmp = "$StateFile.tmp"
    @{ seen = $seen; reminded = $reminded } | ConvertTo-Json | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $StateFile
}

# Same replay rule as the Desk: last 'assign' wins, last valid 'status' wins.
function Get-Handling([string]$folder) {
    $status = "new"; $assignee = ""
    $log = Join-Path $folder "events.jsonl"
    if (Test-Path $log) {
        foreach ($line in [System.IO.File]::ReadAllLines($log)) {
            try { $e = $line | ConvertFrom-Json } catch { continue }
            if ($e.kind -eq "assign") { $assignee = [string]$e.value }
            elseif ($e.kind -eq "status" -and $e.value -in @("new", "investigating", "waiting", "resolved")) { $status = [string]$e.value }
        }
    }
    return @{ status = $status; assignee = $assignee }
}

function Get-RecentIssues {
    # Only today's and yesterday's folders: cheap on a network share, and enough for alerts.
    $days = @((Get-Date).ToString("yyyy-MM-dd"), (Get-Date).AddDays(-1).ToString("yyyy-MM-dd"))
    foreach ($d in $days) {
        $dir = Join-Path $ProcessedPath $d
        if (-not (Test-Path $dir)) { continue }
        foreach ($f in Get-ChildItem -Path $dir -Directory) {
            $ticketFile = Join-Path $f.FullName "ticket.json"
            if (-not (Test-Path $ticketFile)) { continue }  # watchdog still unpacking
            try { $t = [System.IO.File]::ReadAllText($ticketFile) | ConvertFrom-Json } catch { continue }
            $sev = ([string]$t.severity).ToLower()
            if (-not $Rank.ContainsKey($sev)) { $sev = "medium" }
            [pscustomobject]@{
                id = if ($t.id) { [string]$t.id } else { $f.Name }
                folder = $f.FullName
                title = [string]$t.title
                reporter = [string]$t.reporter
                severity = $sev
                created = $(try { [datetime]::Parse([string]$t.created_at).ToUniversalTime() } catch { $f.CreationTimeUtc })
            }
        }
    }
}

function Poll($state) {
    $now = (Get-Date).ToUniversalTime()
    foreach ($i in Get-RecentIssues) {
        $ageMin = ($now - $i.created).TotalMinutes
        if (-not $state.seen.Contains($i.id)) {
            [void]$state.seen.Add($i.id)
            # First start on this PC: don't flood with everything already on the share.
            if ($state.fresh -and $ageMin -gt $CatchUpMinutes) { continue }
            if ($Rank[$i.severity] -ge $Rank[$MinSeverity]) {
                Show-Toast "new" $i.severity $i.title "Reported by $($i.reporter)" $i.id
                Write-Log "notified new $($i.id) [$($i.severity)] $($i.title)"
            }
            continue
        }
        if ($RemindUnassignedMinutes -gt 0 -and $ageMin -ge $RemindUnassignedMinutes -and
                $Rank[$i.severity] -ge $Rank[$RemindMinSeverity] -and -not $state.reminded.Contains($i.id)) {
            $h = Get-Handling $i.folder
            if (-not $h.assignee -and $h.status -ne "resolved") {
                [void]$state.reminded.Add($i.id)
                Show-Toast "reminder" $i.severity $i.title ("Reported {0:N0} min ago by {1}, nobody has taken it" -f $ageMin, $i.reporter) $i.id
                Write-Log "reminded $($i.id)"
            }
        }
    }
    $state.fresh = $false
    Save-State $state
}

if (-not $DryRun) { Register-App }

if ($Test) {
    Show-Toast "new" "high" "Sample: 7203.T sell-short stuck behind VWAP" "Reported by Order Alert notifier test" "test"
    exit 0
}

Write-Log "started: $ProcessedPath (min severity $MinSeverity, remind after $RemindUnassignedMinutes min)"
$state = Load-State
while ($true) {
    try {
        Poll $state
    } catch {
        Write-Log "poll failed (share unreachable?): $_"   # keep running: share blips are normal
    }
    if ($Once) { break }
    Start-Sleep -Seconds $PollSeconds
}
