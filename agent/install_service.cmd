@echo off
setlocal
if "%~1"=="" goto usage
if not exist "%~dp0..\.venv\Scripts\kintler-agent.exe" exit /b 1
if not exist "%~f1" exit /b 1
echo This explicitly installs a visible Kintler Agent ONLOGON task for the current user.
echo For screenshots, the user must be signed in to an interactive desktop.
set /p "CONFIRM=Type INSTALL to continue: "
if /i not "%CONFIRM%"=="INSTALL" exit /b 1
schtasks /Create /TN "Kintler Agent" /TR "\"%~dp0..\.venv\Scripts\kintler-agent.exe\" --env \"%~f1\"" /SC ONLOGON /RL LIMITED
exit /b %ERRORLEVEL%
:usage
echo Usage: install_service.cmd C:\absolute\path\.env
echo Remove explicitly: schtasks /Delete /TN "Kintler Agent"
exit /b 1
