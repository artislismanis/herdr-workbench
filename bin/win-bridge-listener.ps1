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

# Windows PowerShell can't hear a click on its own toast, so a click opens a win-bridge: link
# instead, registered for you alone, whose handler takes you to herdr.
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
try { Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/go.ps1" -Headers @{ 'X-Token' = $Token } -OutFile "$Dir\go.ps1" } catch {}
try {
    $key = 'HKCU:\Software\Classes\win-bridge'
    $null = New-Item -Path "$key\shell\open\command" -Force
    Set-ItemProperty -Path $key -Name '(default)' -Value 'URL:win-bridge'
    Set-ItemProperty -Path $key -Name 'URL Protocol' -Value ''
    Set-ItemProperty -Path "$key\shell\open\command" -Name '(default)' -Value "conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Dir\go.ps1`" `"%1`""
    $CanGo = $true
} catch { $CanGo = $false }

function Toast($e) {
    if (-not $CanToast) { return }
    $link = Allowed $e.url
    # A click goes to herdr where it can, and the link gets a button; otherwise a click opens the link.
    $go = $CanGo -and $e.focus -match '^[0-9a-f]+$'
    $click = if ($go) { " activationType=`"protocol`" launch=`"win-bridge:go?id=$($e.focus)`"" } elseif ($link) { " activationType=`"protocol`" launch=`"$(Esc $e.url)`"" } else { '' }
    $button = if ($go -and $link) { "<actions><action content=`"$(if ($e.url -match '/pull/\d+') { 'Open PR' } else { 'Open link' })`" activationType=`"protocol`" arguments=`"$(Esc $e.url)`"/></actions>" } else { '' }
    $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
    $doc.LoadXml("<toast$click><visual><binding template=`"ToastGeneric`"><text>$(Esc $e.title)</text><text>$(Esc $e.body)</text></binding></visual>$button</toast>")
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
