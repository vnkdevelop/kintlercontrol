@echo off
setlocal
pushd "%~dp0.."
uv run --locked --extra ai kintler console
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
