@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

python -u "tools\manual_tune_prompt.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if /I not "%~1"=="--no-pause" pause
exit /b %EXIT_CODE%
