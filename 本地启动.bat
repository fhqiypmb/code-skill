@echo off
chcp 65001 >nul
REM ============================================================
REM   股票监控本地一键启动
REM   双击运行，选择要启动的任务
REM   放在项目根目录(code-skill)下，自动定位路径，两台机器通用
REM ============================================================

REM ---- 钉钉密钥 ----
set "DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=008d5e2abdd211762a57b67e6dfae8f32e4b6914a8ee0a4ef2c028191c49f2c3"
set "DINGTALK_SECRET=SECb8fb74949e3c99aeb6417a7a1ea78179ddebc6cf3ea25ebabeeb15379bcd310e"
set "POSITION_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=48f12737e55af9180d92f0f4866bc08cce172cd8c19fd062113547cfec3a0dd1"
set "POSITION_DINGTALK_SECRET=SEC69fa75d572dad40751e0d80e50d3a8dedb7a8823a79f46190ac0ef0b31501839"

REM ---- 项目目录（脚本所在目录下的 stocks）----
set "PROJ=%~dp0stocks"
cd /d "%PROJ%"

:menu
echo.
echo ============================================
echo   股票本地监控
echo ============================================
echo   1. 选股监控        (单独窗口，盯到收盘)
echo   2. 持仓盯盘        (单独窗口，盯到收盘)
echo   3. 选股+盯盘 同时跑 (各开一个窗口，并行)
echo   4. 选股监控 --now  (立即扫一轮，测试)
echo   5. 持仓盯盘 --now  (立即跑一轮，测试)
echo   6. ML 周训练       (回填结果+训练模型，每周一跑)
echo   7. 更新股票列表
echo   0. 退出
echo ============================================
set /p choice="请输入数字后回车: "

if "%choice%"=="1" goto monitor
if "%choice%"=="2" goto position
if "%choice%"=="3" goto both
if "%choice%"=="4" goto monitor_now
if "%choice%"=="5" goto position_now
if "%choice%"=="6" goto mltrain
if "%choice%"=="7" goto update_list
if "%choice%"=="0" goto end
echo 无效输入，请重新选择
goto menu

:monitor
echo [启动] 选股监控...
python -u stock_monitor\monitor.py
goto done

:position
echo [启动] 持仓盯盘...
python -u position_monitor\position_monitor.py
goto done

:both
REM 各开一个独立窗口并行跑，环境变量会被子窗口继承
echo [启动] 选股监控 + 持仓盯盘（各开一个窗口）...
start "选股监控" cmd /k "cd /d "%PROJ%" && python -u stock_monitor\monitor.py"
start "持仓盯盘" cmd /k "cd /d "%PROJ%" && python -u position_monitor\position_monitor.py"
echo 已在两个新窗口启动，本窗口可关闭。
goto done

:monitor_now
echo [启动] 选股监控 --now...
python -u stock_monitor\monitor.py --now
goto done

:position_now
echo [启动] 持仓盯盘 --now...
python -u position_monitor\position_monitor.py --now
goto done

:mltrain
echo [启动] ML 周训练（回填实际结果 + 训练模型）...
python -u ml\weekly_train.py
goto done

:update_list
echo [启动] 更新股票列表...
python "更新股票列表.py"
goto done

:done
echo.
echo ============================================
echo   任务结束
echo ============================================
pause
goto menu

:end
exit
