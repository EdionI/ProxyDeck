@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ProxyDeck - First-time setup

echo.
echo ========================================
echo        ProxyDeck setup
echo ========================================
echo.

if not exist "%~dp0main.py" goto :missing_files
if not exist "%~dp0proxy_manager.py" goto :missing_files
if not exist "%~dp0proxies.txt" goto :missing_files
if not exist "%~dp0proxydeck_data" goto :missing_files

call :find_python
if defined PYTHON_CMD goto :python_ready

echo Python with Tkinter was not found.
echo Installing the bundled official Python 3.14.7 for this Windows user...
echo.
set "PYTHON_INSTALLER=%~dp0installers\python-3.14.7-amd64.exe"
if not exist "%PYTHON_INSTALLER%" goto :missing_installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-AuthenticodeSignature -LiteralPath '%PYTHON_INSTALLER%'; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Python Software Foundation') { exit 1 }"
if errorlevel 1 goto :invalid_installer

start /wait "Python installer" "%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_pip=1 Include_test=0 Include_doc=0
if errorlevel 1 goto :python_install_failed

call :find_python
if not defined PYTHON_CMD goto :python_install_failed

:python_ready
echo Python is ready.
call :find_chrome
if defined CHROME_FOUND goto :chrome_ready

echo.
echo Google Chrome was not found. ProxyDeck needs Chrome to open workspaces.
echo Downloading the official Chrome installer...
set "CHROME_INSTALLER=%TEMP%\ProxyDeck_ChromeSetup.exe"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -UseBasicParsing 'https://dl.google.com/chrome/install/ChromeStandaloneSetup64.exe' -OutFile '%CHROME_INSTALLER%'; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto :chrome_download_failed
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$s = Get-AuthenticodeSignature -LiteralPath '%CHROME_INSTALLER%'; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Google') { exit 1 }"
if errorlevel 1 goto :chrome_invalid_installer
start /wait "Chrome installer" "%CHROME_INSTALLER%"
del /q "%CHROME_INSTALLER%" >nul 2>&1
call :find_chrome
if not defined CHROME_FOUND goto :chrome_install_failed

:chrome_ready
echo Chrome is ready.
echo Creating a desktop shortcut...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Create_Desktop_Shortcut.ps1" >nul 2>&1

echo.
echo Setup complete. Starting ProxyDeck...
start "ProxyDeck" %PYTHONW_CMD% "%~dp0main.py"
exit /b 0

:find_python
set "PYTHON_CMD="
set "PYTHONW_CMD="
py.exe -3 -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py.exe -3"
    set "PYTHONW_CMD=pyw.exe -3"
    exit /b 0
)
python.exe -c "import tkinter" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python.exe"
    set "PYTHONW_CMD=pythonw.exe"
    exit /b 0
)
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    "%LocalAppData%\Programs\Python\Python314\python.exe" -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=""%LocalAppData%\Programs\Python\Python314\python.exe"""
        set "PYTHONW_CMD=""%LocalAppData%\Programs\Python\Python314\pythonw.exe"""
    )
)
exit /b 0

:find_chrome
set "CHROME_FOUND="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
exit /b 0

:missing_files
echo ERROR: The transfer was not fully extracted.
echo Right-click the ZIP, choose "Extract All", then run this file from the extracted folder.
goto :failed

:missing_installer
echo ERROR: The bundled Python installer is missing.
echo Re-extract the complete transfer ZIP and try again.
goto :failed

:invalid_installer
echo ERROR: The bundled Python installer failed its digital-signature check.
echo Re-copy and re-extract the transfer ZIP before trying again.
goto :failed

:python_install_failed
echo ERROR: Python could not be installed automatically.
echo Install 64-bit Python from https://www.python.org/downloads/windows/
echo Make sure "Tcl/Tk and IDLE" is selected, then run this setup again.
goto :failed

:chrome_download_failed
echo ERROR: Chrome could not be downloaded. Check the laptop's internet connection.
echo Install Chrome from https://www.google.com/chrome/ and run this setup again.
goto :failed

:chrome_invalid_installer
del /q "%CHROME_INSTALLER%" >nul 2>&1
echo ERROR: The downloaded Chrome installer failed its digital-signature check.
echo Install Chrome from https://www.google.com/chrome/ and run this setup again.
goto :failed

:chrome_install_failed
echo ERROR: Chrome installation did not finish.
echo Install Chrome manually, then run this setup again.
goto :failed

:failed
echo.
pause
exit /b 1
