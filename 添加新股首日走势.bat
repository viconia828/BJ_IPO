@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if /I "%~1"=="1" goto run_intraday
if /I "%~1"=="2" goto run_dataset
if /I "%~1"=="Q" goto quit

echo.
echo 请选择要执行的刷新任务：
echo.
echo   1. 刷新新股首日走势
echo   2. 刷新新上市新股数据（估值 replay / 申购 history / 缺公告重试）
echo   Q. 退出
echo.

choice /C 12Q /N /M "请输入选项 [1/2/Q]: "
if errorlevel 3 goto quit
if errorlevel 2 goto run_dataset
if errorlevel 1 goto run_intraday

:run_dataset
echo.
echo 正在刷新新上市新股数据：估值 replay、申购 history、手工阶梯标签上下文、样本 manifest...
echo 缺失的发行公告/发行结果公告会自动尝试下载；未取到或字段未齐的代码会保留待重试标记。
python -u "tools\sync_offline_tuning_dataset.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:run_intraday
echo.
echo 正在刷新新股首日走势...
python -u "tools\add_new_ipo_intraday_cache.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:quit
set "EXIT_CODE=0"

:done
echo.
if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
exit /b %EXIT_CODE%
