@echo off
setlocal EnableExtensions DisableDelayedExpansion
if "%LOCALAPPDATA%"=="" exit /b 2
if not "%~1"=="" if /i not "%~1"=="--check" exit /b 2
set "install_root=%LOCALAPPDATA%\Programs\KintlerControl"
set "controller=%LOCALAPPDATA%\Programs\KintlerControl\KintlerController.exe"
set "temporary_root=%LOCALAPPDATA%\Temp\KintlerControl"
set "stage=%LOCALAPPDATA%\Temp\KintlerControl\kintler-install-%RANDOM%-%RANDOM%"
set "incoming=%LOCALAPPDATA%\Programs\KintlerControl\KintlerController.install-%RANDOM%-%RANDOM%.exe"
setlocal EnableDelayedExpansion
if exist "!controller!" (
    echo Controller already installed: "!controller!"
    exit /b 0
)
if /i "%~1"=="--check" (
    echo Controller is not installed.
    exit /b 3
)
where curl.exe >nul 2>&1
if errorlevel 1 exit /b 2
where certutil.exe >nul 2>&1
if errorlevel 1 exit /b 2
if exist "!stage!" exit /b 2
if exist "!incoming!" exit /b 2
if not exist "!temporary_root!" mkdir "!temporary_root!"
if errorlevel 1 exit /b 2
mkdir "!stage!"
if errorlevel 1 exit /b 2
set "result=1"
curl.exe --fail --location --silent --show-error --proto "=https" --proto-redir "=https" --connect-timeout 15 --max-time 300 --retry 2 --output "!stage!\SHA256SUMS.txt" "https://github.com/vnkdevelop/kintlercontrol/releases/latest/download/SHA256SUMS.txt"
if errorlevel 1 goto finish
curl.exe --fail --location --silent --show-error --proto "=https" --proto-redir "=https" --connect-timeout 15 --max-time 300 --retry 2 --output "!stage!\KintlerController.exe" "https://github.com/vnkdevelop/kintlercontrol/releases/latest/download/KintlerController.exe"
if errorlevel 1 goto finish
set "expected="
set /a checksum_entries=0
find.exe /v "" <"!stage!\SHA256SUMS.txt" >"!stage!\manifest.txt"
if errorlevel 1 goto invalid
findstr.exe /r /x /c:"[0-9A-Fa-f][0-9A-Fa-f]*  KintlerController[.]exe" "!stage!\manifest.txt" >"!stage!\expected.txt"
if errorlevel 1 goto invalid
for /f "usebackq tokens=1" %%A in ("!stage!\expected.txt") do (
    set "expected=%%A"
    set /a checksum_entries+=1
)
if not "!checksum_entries!"=="1" goto invalid
if "!expected:~63,1!"=="" goto invalid
if not "!expected:~64,1!"=="" goto invalid
certutil.exe -hashfile "!stage!\KintlerController.exe" SHA256 >"!stage!\checksum.txt" 2>nul
if errorlevel 1 goto invalid
set "actual="
findstr.exe /r /x "[0-9A-Fa-f][0-9A-Fa-f]*" "!stage!\checksum.txt" >"!stage!\actual.txt"
if errorlevel 1 goto invalid
for /f "usebackq tokens=1" %%A in ("!stage!\actual.txt") do set "actual=%%A"
if /i not "!actual!"=="!expected!" goto invalid
if not exist "!stage!\KintlerController.exe" goto invalid
start "" /wait "!stage!\KintlerController.exe" --self-test "!stage!\startup.json"
if errorlevel 1 goto invalid
if not exist "!stage!\startup.json" goto invalid
if not exist "!install_root!" mkdir "!install_root!"
if errorlevel 1 goto finish
if exist "!controller!" goto success
copy /b "!stage!\KintlerController.exe" "!incoming!" >nul
if errorlevel 1 goto finish
ren "!incoming!" KintlerController.exe >nul 2>&1
if errorlevel 1 (
    if not exist "!controller!" goto finish
)
:success
echo Controller ready: "!controller!"
echo Owner profiles and existing installations were not changed. MCP registration is a separate step.
set "result=0"
goto finish
:invalid
echo Installation refused: release checksum, extraction or startup validation failed.
:finish
if exist "!incoming!" del /q "!incoming!"
if exist "!stage!" rmdir /s /q "!stage!"
if not "!result!"=="0" echo Controller was not installed; no owner profile was changed.
exit /b !result!
