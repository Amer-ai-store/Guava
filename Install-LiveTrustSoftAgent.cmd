@echo off
powershell.exe -NoLogo -NoProfile -STA -File "%~dp0Install-LiveTrustSoftAgent.ps1"
set "TrustSoftResult=%ERRORLEVEL%"
pause
exit /b %TrustSoftResult%
