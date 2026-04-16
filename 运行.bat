@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

python "bse_ipo_valuation.py"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
pause
exit /b %EXIT_CODE%
