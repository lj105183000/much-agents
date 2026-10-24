@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

where uv >nul 2>nul
if errorlevel 1 (
    echo [error] 未找到 uv，请先安装 uv 后再运行。
    echo         https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

uv run python scripts\start_all.py %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo 启动失败，退出码 %EXIT_CODE%。请查看上方错误信息。
    pause
)
exit /b %EXIT_CODE%
