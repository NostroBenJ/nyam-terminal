@echo off
REM Deploy the built app into the portable folder.
REM
REM A .bat wrapper because PowerShell's execution policy blocks running a .ps1
REM from a file by default, and the alternative — Set-ExecutionPolicy — is a
REM machine-wide security setting changed permanently to run one script. The
REM -ExecutionPolicy Bypass flag applies to THIS invocation only and changes
REM nothing about the system.
REM
REM CLOSE NYAM TERMINAL FIRST. Windows locks nyam-terminal.exe while it runs,
REM so the shell copy fails and the frontend silently stays on the old build.
REM The script now catches that and refuses rather than reporting success, but
REM it cannot copy over a running app.

echo.
echo  Close NYAM Terminal before continuing, or the frontend will not update.
echo.
pause

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1"

echo.
pause
