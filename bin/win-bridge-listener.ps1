# win-bridge's Windows side, filled in by win-bridge when it's fetched. Run it with
# Windows PowerShell (powershell.exe), not pwsh: toasts need its WinRT types.
$Port = __PORT__
$Token = '__TOKEN__'
$Hosts = @(__HOSTS__)
$App = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

try {
    $one = New-Object System.Threading.Mutex($false, "Local\win-bridge-$Port")
    if (-not $one.WaitOne(0)) { exit }
} catch {}

try {
    $null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    $null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
    $CanToast = $true
} catch { $CanToast = $false }

function Allowed($url) {
    $u = $null
    if (-not [uri]::TryCreate([string]$url, 'Absolute', [ref]$u) -or $u.Scheme -ne 'https') { return $false }
    foreach ($h in $Hosts) { if ($u.Host -eq $h -or $u.Host.EndsWith(".$h")) { return $true } }
    return $false
}

function Esc($s) { [Security.SecurityElement]::Escape([string]$s) }

Add-Type -AssemblyName System.Net.Http
$Http = New-Object System.Net.Http.HttpClient
$Http.Timeout = [TimeSpan]::FromSeconds(40)
$Http.DefaultRequestHeaders.Add('X-Token', $Token)
$Base = "http://127.0.0.1:$Port"
$Shown = New-Object System.Collections.Queue

# A click on a toast goes to its herdr workspace, then brings Windows Terminal forward.
function Clicked {
    foreach ($ev in @(Get-Event | Where-Object { $_.SourceIdentifier -like 'win-bridge-*' })) {
        Remove-Event -EventIdentifier $ev.EventIdentifier
        try { $null = $Http.GetAsync("$Base/focus?id=$($ev.MessageData)").Result } catch {}
        $wt = Get-Process WindowsTerminal -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0 | Select-Object -First 1
        if ($wt) { try { $null = (New-Object -ComObject WScript.Shell).AppActivate($wt.Id) } catch {} }
    }
}

function Toast($e) {
    if (-not $CanToast) { return }
    $link = Allowed $e.url
    # With a workspace to go to, the click goes there and the link gets a button; without one, the click opens the link.
    $click = if ($e.focus) { '' } elseif ($link) { " activationType=`"protocol`" launch=`"$(Esc $e.url)`"" } else { '' }
    $button = if ($e.focus -and $link) { "<actions><action content=`"$(if ($e.url -match '/pull/\d+') { 'Open PR' } else { 'Open link' })`" activationType=`"protocol`" arguments=`"$(Esc $e.url)`"/></actions>" } else { '' }
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml("<toast$click><visual><binding template=`"ToastGeneric`"><text>$(Esc $e.title)</text><text>$(Esc $e.body)</text></binding></visual>$button</toast>")
    $toast = New-Object Windows.UI.Notifications.ToastNotification $doc
    if ($e.focus) {
        $id = "win-bridge-$($e.focus)"
        $null = Register-ObjectEvent -InputObject $toast -EventName Activated -SourceIdentifier $id -MessageData $e.focus
        $Shown.Enqueue($id)
        while ($Shown.Count -gt 50) { Unregister-Event -SourceIdentifier $Shown.Dequeue() -ErrorAction SilentlyContinue }
    }
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($App).Show($toast)
}

$wait = 1
while ($true) {
    try {
        # Waits a little at a time, so a click is acted on while the poll is still open.
        $task = $Http.GetAsync("$Base/next")
        while (-not $task.Wait(250)) { Clicked }
        $r = $task.Result
        $wait = 1
    } catch {
        # WSL may be stopped or the server restarting: back off quietly.
        Start-Sleep -Seconds $wait
        $wait = [Math]::Min($wait * 2, 30)
        continue
    }
    Clicked
    if ([int]$r.StatusCode -ne 200) { continue }
    try {
        $e = $r.Content.ReadAsStringAsync().Result | ConvertFrom-Json
        if ($e.kind -eq 'open' -and (Allowed $e.url)) { Start-Process $e.url }
        elseif ($e.kind -eq 'toast') { Toast $e }
    } catch {}
}
