@echo off
powershell.exe -NoLogo -NoProfile -STA -File "%~dp0Run-UploadedSqlCheck.ps1"
set "TrustSoftResult=%ERRORLEVEL%"
pause
exit /b %TrustSoftResult%
