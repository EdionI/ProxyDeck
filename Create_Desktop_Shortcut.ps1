$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$setupFile = Join-Path $projectDir "START_HERE_Setup_ProxyDeck.bat"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "ProxyDeck.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $setupFile
$shortcut.WorkingDirectory = $projectDir
$shortcut.Description = "Start ProxyDeck"
$shortcut.Save()
