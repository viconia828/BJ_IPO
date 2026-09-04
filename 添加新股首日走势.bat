@echo off
setlocal
chcp 65001 >nul
rem Keep this file ASCII-only. Render localized text through ipo_refresh_console.py.
cd /d "%~dp0"

if /I "%~1"=="1" goto run_intraday
if /I "%~1"=="2" goto run_dataset
if /I "%~1"=="3" goto run_xueqiu
if /I "%~1"=="Q" goto quit

call python -X utf8 -u "tools\ipo_refresh_console.py" menu
set "MENU_EXIT=%ERRORLEVEL%"
if "%MENU_EXIT%"=="4" goto quit
if "%MENU_EXIT%"=="3" goto run_xueqiu
if "%MENU_EXIT%"=="2" goto run_dataset
if "%MENU_EXIT%"=="1" goto run_intraday
set "EXIT_CODE=%MENU_EXIT%"
goto done

:run_dataset
call python -X utf8 -u "tools\ipo_refresh_console.py" dataset
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto done
call python -X utf8 -u "tools\sync_offline_tuning_dataset.py" --sync-prospectus-documents
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:run_intraday
call python -X utf8 -u "tools\ipo_refresh_console.py" intraday
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto done
call python -X utf8 -u "tools\add_new_ipo_intraday_cache.py"
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:run_xueqiu
echo.
echo Refreshing Xueqiu reference from local MHTML/TXT only. Network collection is disabled.
if not exist "xueqiu" mkdir "xueqiu"
call python -X utf8 -u "tools\refresh_xueqiu_reference.py" --input-dir "xueqiu"
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:quit
set "EXIT_CODE=0"

:done
echo.
if /I not "%~1"=="--no-pause" if /I not "%~2"=="--no-pause" pause
exit /b %EXIT_CODE%
