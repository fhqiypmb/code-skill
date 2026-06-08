@echo off
chcp 65001 >nul
REM ============================================================
REM   自动启动脚本（供任务计划调用，静默后台运行，无窗口）
REM   同时跑 选股监控 + 持仓盯盘
REM   非交易日/收盘后 monitor 内部会自动退出
REM ============================================================

REM ---- 钉钉密钥 ----
set "DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=008d5e2abdd211762a57b67e6dfae8f32e4b6914a8ee0a4ef2c028191c49f2c3"
set "DINGTALK_SECRET=SECb8fb74949e3c99aeb6417a7a1ea78179ddebc6cf3ea25ebabeeb15379bcd310e"
set "POSITION_DINGTALK_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=48f12737e55af9180d92f0f4866bc08cce172cd8c19fd062113547cfec3a0dd1"
set "POSITION_DINGTALK_SECRET=SEC69fa75d572dad40751e0d80e50d3a8dedb7a8823a79f46190ac0ef0b31501839"

set "PROJ=%~dp0stocks"
cd /d "%PROJ%"

REM 日志目录
if not exist "%~dp0logs" mkdir "%~dp0logs"
set "LOGDIR=%~dp0logs"
set "TODAY=%date:~0,4%-%date:~5,2%-%date:~8,2%"

REM 静默后台启动（pythonw 无窗口），输出重定向到日志文件，方便排查
start "" /b pythonw -u stock_monitor\monitor.py        > "%LOGDIR%\monitor_%TODAY%.log" 2>&1
start "" /b pythonw -u position_monitor\position_monitor.py > "%LOGDIR%\position_%TODAY%.log" 2>&1
