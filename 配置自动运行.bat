@echo off
chcp 65001 >nul
REM ============================================================
REM   一键配置自动运行（双击一次即可，以后每个工作日自动跑）
REM   注册两个 Windows 任务计划：
REM     - 选股+盯盘：每工作日 08:50 静默启动
REM     - ML周训练：每周一 18:00
REM ============================================================

set "PROJ=%~dp0"
set "AUTOBAT=%PROJ%自动启动.bat"
set "TRAINBAT=%PROJ%周训练.bat"

echo ============================================
echo   配置股票监控自动运行
echo ============================================
echo.
echo 将注册以下任务计划：
echo   1) 选股+盯盘  每工作日 08:50 自动启动
echo   2) ML周训练   每周一   18:00 自动运行
echo.
echo 注意：需要电脑在对应时间处于开机状态。
echo.
pause

REM --- 任务1：每工作日 08:50 启动选股+盯盘 ---
schtasks /create /tn "股票_选股盯盘" /tr "\"%AUTOBAT%\"" /sc weekly /d MON,TUE,WED,THU,FRI /st 08:50 /f
if %errorlevel%==0 (echo [OK] 已注册：选股+盯盘 工作日08:50) else (echo [失败] 选股盯盘任务注册失败，可能需要管理员权限)

REM --- 任务2：每周一 18:00 ML训练 ---
schtasks /create /tn "股票_ML周训练" /tr "\"%TRAINBAT%\"" /sc weekly /d MON /st 18:00 /f
if %errorlevel%==0 (echo [OK] 已注册：ML周训练 周一18:00) else (echo [失败] ML训练任务注册失败)

echo.
echo ============================================
echo   配置完成
echo ============================================
echo.
echo 以后每个工作日 08:50 自动跑，不用手动点。
echo.
echo 【如何修改/取消】
echo   - 取消选股盯盘: schtasks /delete /tn "股票_选股盯盘" /f
echo   - 取消ML训练:   schtasks /delete /tn "股票_ML周训练" /f
echo   - 改时间/查看: 打开"任务计划程序"(taskschd.msc)，找到上面两个任务名修改
echo.
pause
