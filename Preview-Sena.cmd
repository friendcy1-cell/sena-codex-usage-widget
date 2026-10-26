@echo off
if exist "%~dp0SenaUsageWidget.exe" (
    start "" "%~dp0SenaUsageWidget.exe" --preview --language en --details
) else (
    powershell.exe -NoProfile -File "%~dp0Start-Sena.ps1" -Preview
)
