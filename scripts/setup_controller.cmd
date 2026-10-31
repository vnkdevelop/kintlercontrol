@echo off
setlocal
where uv >nul 2>nul
if errorlevel 1 goto missing
pushd "%~dp0.."
uv sync --locked --python 3.12 --extra ai
if errorlevel 1 goto failed
uv run --no-sync kintler login
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
:failed
popd
exit /b 1
:missing
echo Install uv from https://docs.astral.sh/uv/getting-started/installation/ then retry.
exit /b 1
