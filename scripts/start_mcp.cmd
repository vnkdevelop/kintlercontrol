@echo off
setlocal
pushd "%~dp0.."
uv run --locked --extra ai kintler-mcp --env "%CD%\.env"
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
