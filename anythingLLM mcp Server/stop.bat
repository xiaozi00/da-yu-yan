@echo off
chcp 65001 >nul
REM Stop AnythingLLM MCP Server (process listening on port 8000)

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Stopping MCP server, PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)

if "%FOUND%"=="0" (
    echo Nothing is listening on port 8000. Server is not running.
) else (
    echo MCP server stopped.
)
echo.
pause
