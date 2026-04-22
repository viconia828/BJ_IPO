@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

python "tools\manual_tune_prompt.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not defined CODEX_BATCH_NO_PAUSE pause
exit /b %EXIT_CODE%
