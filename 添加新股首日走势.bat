@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if /I "%~1"=="1" goto run_intraday
if /I "%~1"=="2" goto run_dataset
if /I "%~1"=="3" goto run_xueqiu
if /I "%~1"=="Q" goto quit

echo.
echo 请选择要执行的刷新任务：
echo.
echo   1. 刷新新股首日走势
echo   2. 刷新新上市新股数据（估值 replay / 申购 history / 缺公告重试）
echo   3. Refresh Xueqiu reference from manual files in xueqiu folder
echo   Q. 退出
echo.

choice /C 123Q /N /M "请输入选项 [1/2/3/Q]: "
if errorlevel 4 goto quit
if errorlevel 3 goto run_xueqiu
if errorlevel 2 goto run_dataset
if errorlevel 1 goto run_intraday

:run_dataset
echo.
echo 正在刷新新上市新股数据：估值 replay、申购 history、手工阶梯标签上下文、样本 manifest...
echo 上市前招股文件按增量规则补齐：已上市代码不再更新；未上市且本地缺正式招股说明书的代码才查询官网。
echo 缺失的发行公告、发行结果公告会自动尝试下载；未取到或字段未齐的代码会保留待重试标记。
python -u "tools\sync_offline_tuning_dataset.py" --sync-prospectus-documents
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:run_intraday
echo.
echo 正在刷新新股首日走势...
python -u "tools\add_new_ipo_intraday_cache.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:run_xueqiu
echo.
echo Refreshing Xueqiu reference from local MHTML/TXT only. Network collection is disabled.
if not exist "xueqiu" mkdir "xueqiu"
python -u "tools\refresh_xueqiu_reference.py" --input-dir "xueqiu"
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:quit
set "EXIT_CODE=0"

:done
echo.
if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
exit /b %EXIT_CODE%
