@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ProxyDeck
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup_ProxyDeck.ps1"
if errorlevel 1 (
    echo.
    echo ProxyDeck could not start. See the error above.
    pause
    exit /b 1
)
