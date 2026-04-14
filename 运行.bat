@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ====================================
echo  北交所新股上市首日估值框架
echo ====================================
echo.

set /p CODE=请输入新股代码(如920012):
python bse_ipo_valuation.py %CODE%

echo.
pause
