@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -File "%~dp0Run-TrustSoftCheck.ps1" %*
set "TrustSoftExit=%ERRORLEVEL%"
echo.
echo Diagnostic process exit code: %TrustSoftExit%
echo Keep the report ZIP from the reports folder. No export was started.
pause
exit /b %TrustSoftExit%
