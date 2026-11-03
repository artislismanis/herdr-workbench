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

function Toast($e) {
    if (-not $CanToast) { return }
    $click = if (Allowed $e.url) { " activationType=`"protocol`" launch=`"$(Esc $e.url)`"" } else { '' }
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml("<toast$click><visual><binding template=`"ToastGeneric`"><text>$(Esc $e.title)</text><text>$(Esc $e.body)</text></binding></visual></toast>")
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($App).Show((New-Object Windows.UI.Notifications.ToastNotification $doc))
}

$wait = 1
while ($true) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/next" -Headers @{ 'X-Token' = $Token } -TimeoutSec 40
        $wait = 1
    } catch {
        # WSL may be stopped or the server restarting: back off quietly.
        Start-Sleep -Seconds $wait
        $wait = [Math]::Min($wait * 2, 30)
        continue
    }
    if ($r.StatusCode -ne 200) { continue }
    try {
        $e = $r.Content | ConvertFrom-Json
        if ($e.kind -eq 'open' -and (Allowed $e.url)) { Start-Process $e.url }
        elseif ($e.kind -eq 'toast') { Toast $e }
    } catch {}
}
