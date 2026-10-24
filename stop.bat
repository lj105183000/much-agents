@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto found
where uv >nul 2>nul
if errorlevel 1 goto missing
uv run python scripts\start_all.py --stop
goto done

:found
".venv\Scripts\python.exe" scripts\start_all.py --stop
goto done

:missing
echo [error] 未找到 .venv 或 uv，无法停止由一键启动拉起的服务。

:done
pause
