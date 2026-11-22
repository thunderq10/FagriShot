# Drive only the folder dialog opened by this isolated launcher verification process.
param([Parameter(Mandatory)][int]$LauncherPid, [Parameter(Mandatory)][string]$Kind,
      [Parameter(Mandatory)][string]$Folder)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ae = [System.Windows.Automation.AutomationElement]
$scope = [System.Windows.Automation.TreeScope]
$condition = [System.Windows.Automation.PropertyCondition]
$root = $ae::RootElement
$dialogFilter = [System.Windows.Automation.AndCondition]::new(
    $condition::new($ae::ProcessIdProperty, $LauncherPid),
    $condition::new($ae::NameProperty, "Select extracted $Kind"))
$until = [DateTime]::UtcNow.AddSeconds(25)
do {
    $dialog = $root.FindFirst($scope::Children, $dialogFilter)
    if (!$dialog) { Start-Sleep -Milliseconds 100 }
} until ($dialog -or [DateTime]::UtcNow -gt $until)
if (!$dialog) { throw "The launcher's native $Kind picker did not open." }
# The common dialog exposes its Folder edit as a native control. On some Windows
# versions UI Automation describes it as a Pane and offers no ValuePattern.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class PlayerFolderDialog {
    [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr parent, int id);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessageW(IntPtr window, uint message, IntPtr wparam, string text);
    [DllImport("user32.dll")] public static extern bool PostMessageW(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
}
"@
$window = [IntPtr]$dialog.Current.NativeWindowHandle
$until = [DateTime]::UtcNow.AddSeconds(10)
do {
    $entry = [PlayerFolderDialog]::GetDlgItem($window, 1152)
    $accept = [PlayerFolderDialog]::GetDlgItem($window, 1)
    if (!$entry -or !$accept) { Start-Sleep -Milliseconds 100 }
} until (($entry -ne [IntPtr]::Zero -and $accept -ne [IntPtr]::Zero) -or [DateTime]::UtcNow -gt $until)
if ($entry -eq [IntPtr]::Zero -or $accept -eq [IntPtr]::Zero) { throw "The $Kind picker has no folder field or Select Folder button." }
if (![PlayerFolderDialog]::SendMessageW($entry, 12, [IntPtr]::Zero, $Folder).ToInt64()) { throw 'The folder field rejected the selected path.' }
if (![PlayerFolderDialog]::PostMessageW($accept, 245, [IntPtr]::Zero, [IntPtr]::Zero)) { throw 'Could not choose Select Folder.' }
