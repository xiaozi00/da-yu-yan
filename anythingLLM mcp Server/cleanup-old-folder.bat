@echo off
chcp 65001 >nul
REM One-time cleanup: delete the OLD project folder D:\mcp.
REM The project has already been copied to "D:\da yu yan\anythingLLM mcp Server".
REM Run this AFTER closing the WorkBuddy session that keeps D:\mcp open.

if not exist "D:\mcp" (
    echo D:\mcp does not exist. Nothing to do.
    goto :end
)

echo Contents of D:\mcp:
dir /b "D:\mcp"
echo.
set /p CONFIRM=Delete D:\mcp now? [y/N] 
if /i not "%CONFIRM%"=="y" goto :end

rd /s /q "D:\mcp"
if exist "D:\mcp" (
    echo.
    echo FAILED. The folder is locked. Close WorkBuddy and any Explorer window, then run again.
) else (
    echo.
    echo D:\mcp has been deleted.
)

:end
echo.
pause
