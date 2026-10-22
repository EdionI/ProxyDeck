$ErrorActionPreference = 'Stop'
$appDir = $PSScriptRoot
$appExe = Join-Path $appDir 'ProxyDeck.exe'

function Find-Chrome {
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if ($base) {
            $candidate = Join-Path $base 'Google\Chrome\Application\chrome.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    }
    foreach ($hive in @('HKCU:', 'HKLM:')) {
        $key = "$hive\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        if (Test-Path -LiteralPath $key) {
            $candidate = (Get-Item -LiteralPath $key).GetValue('')
            if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                return $candidate
            }
        }
    }
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'This package requires 64-bit Windows.'
    }
    if (-not (Test-Path -LiteralPath $appExe -PathType Leaf)) {
        throw 'Extract the entire RAR into a folder first. ProxyDeck.exe is missing.'
    }
    $probe = Join-Path $appDir ([IO.Path]::GetRandomFileName())
    try { [IO.File]::WriteAllText($probe, '') }
    finally { if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe } }

    if (-not (Find-Chrome)) {
        Write-Host 'Chrome is missing. Downloading the official Google installer...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $downloadDir = Join-Path ([IO.Path]::GetTempPath()) ('ProxyDeck-' + [Guid]::NewGuid())
        New-Item -ItemType Directory -Path $downloadDir | Out-Null
        $installer = Join-Path $downloadDir 'ChromeSetup.exe'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe' -OutFile $installer
            $signature = Get-AuthenticodeSignature -LiteralPath $installer
            if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Google (LLC|Inc)') {
                throw 'Chrome installer publisher verification failed.'
            }
            Write-Host 'Installing Chrome. Follow any installer prompts...'
            $process = Start-Process -FilePath $installer -Wait -PassThru -WindowStyle Hidden
            if ($process.ExitCode -notin @(0, 3010)) {
                throw "Chrome installer returned code $($process.ExitCode)."
            }
            if (-not (Find-Chrome)) { throw 'Chrome was not found after installation.' }
        }
        finally {
            if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -Force }
            # Remove only our own now-empty temporary directory.
            Remove-Item -LiteralPath $downloadDir -ErrorAction SilentlyContinue
        }
    }

    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $desktop 'ProxyDeck.lnk'))
        $shortcut.TargetPath = Join-Path $appDir 'START_ProxyDeck.bat'
        $shortcut.WorkingDirectory = $appDir
        $shortcut.IconLocation = "$appExe,0"
        $shortcut.Description = 'Open ProxyDeck'
        $shortcut.Save()
    }
    catch { Write-Warning 'Desktop shortcut could not be created. Use START_ProxyDeck.bat.' }

    Start-Process -FilePath $appExe -WorkingDirectory $appDir -WindowStyle Hidden
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Use a writable extracted folder. If Chrome setup failed, install Chrome from https://www.google.com/chrome/ and try again.'
    exit 1
}
