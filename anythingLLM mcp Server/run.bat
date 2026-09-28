@echo off
chcp 65001 >nul
REM ============================================================
REM  AnythingLLM MCP Server 启动脚本
REM  前置条件：AnythingLLM 桌面端已启动（监听 3001）
REM ============================================================

REM ---------- 1. API Key：从环境变量读取，不写入仓库 ----------
REM 首次使用请先执行一次（把 <你的 Key> 换成实际值），然后重开命令行窗口：
REM     setx ANYTHINGLLM_API_KEY "<你的 Key>"
if "%ANYTHINGLLM_API_KEY%"=="" (
    echo [ERROR] 环境变量 ANYTHINGLLM_API_KEY 未设置。
    echo         请先执行:  setx ANYTHINGLLM_API_KEY "你的 AnythingLLM API Key"
    echo         然后重新打开一个命令行窗口再运行本脚本。
    echo.
    pause
    exit /b 1
)

if "%ANYTHINGLLM_BASE_URL%"=="" set ANYTHINGLLM_BASE_URL=http://localhost:3001

REM ---------- 2. Python 解释器解析顺序 ----------
REM   1) 环境变量 MCP_PYTHON 指定的路径
REM   2) 脚本同目录下的 .venv\Scripts\python.exe
REM   3) PATH 中的 python
if defined MCP_PYTHON goto :have_python
if exist "%~dp0.venv\Scripts\python.exe" set "MCP_PYTHON=%~dp0.venv\Scripts\python.exe"
if defined MCP_PYTHON goto :have_python
set "MCP_PYTHON=python"
:have_python

echo Starting MCP server on http://127.0.0.1:8000/mcp ...
"%MCP_PYTHON%" "%~dp0server.py"
pause
