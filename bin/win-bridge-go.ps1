# win-bridge's click handler on Windows, filled in by win-bridge and run through
# the win-bridge-<port>: link a toast click opens. It asks WSL to focus the toast's
# workspace, then finds herdr's tab by the titles WSL says it may have (herdr's
# own, then herdr, then the distro's), in whichever Windows Terminal window holds it, and
# brings that forward.
if ("$args" -notmatch 'id=([0-9a-f]+)') { exit }
try {
    $titles = @(([string](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:__PORT__/focus?id=$($Matches[1])" -Headers @{ 'X-Token' = '__TOKEN__' } -TimeoutSec 10).Content) -split "`n" | Where-Object { $_ })
} catch { exit }
if (-not $titles) { exit }

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
# Windows lets a program bring a window forward only just after a key press, so this presses Alt first.
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Front {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr window, int how);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
}
'@
function Forward([IntPtr]$window) {
    if ([Front]::IsIconic($window)) { $null = [Front]::ShowWindow($window, 9) }
    [Front]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
    $null = [Front]::SetForegroundWindow($window)
    [Front]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
}
$A = [Windows.Automation.AutomationElement]
$terminal = New-Object Windows.Automation.PropertyCondition($A::ClassNameProperty, 'CASCADIA_HOSTING_WINDOW_CLASS')
$tab = New-Object Windows.Automation.PropertyCondition($A::ControlTypeProperty, [Windows.Automation.ControlType]::TabItem)
# herdr retitles its tab a moment after the focus, so look for a couple of seconds.
for ($i = 0; $i -lt 10; $i++) {
    foreach ($title in $titles) {
        foreach ($window in $A::RootElement.FindAll('Children', $terminal)) {
            foreach ($t in $window.FindAll('Descendants', $tab)) {
                if ($t.Current.Name -ne $title) { continue }
                try { $t.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select() } catch {}
                Forward ([IntPtr]$window.Current.NativeWindowHandle)
                exit
            }
        }
    }
    Start-Sleep -Milliseconds 200
}
