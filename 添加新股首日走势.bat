@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

python "tools\add_new_ipo_intraday_cache.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
pause
exit /b %EXIT_CODE%
