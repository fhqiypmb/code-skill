@echo off
chcp 65001 >nul
REM ============================================================
REM   ML 周训练（供任务计划每周一调用，也可手动双击）
REM   回填实际结果 + 训练模型
REM ============================================================

set "PROJ=%~dp0stocks"
cd /d "%PROJ%"

if not exist "%~dp0logs" mkdir "%~dp0logs"
set "TODAY=%date:~0,4%-%date:~5,2%-%date:~8,2%"

python -u ml\weekly_train.py > "%~dp0logs\train_%TODAY%.log" 2>&1
