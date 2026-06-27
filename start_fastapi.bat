@echo off
chcp 65001 >nul
cd /d "%~dp0backend"
"E:\code_workplace\Anaconda_envs\envs\Agens\python.exe" -B -m uvicorn main:app --host 127.0.0.1 --port 6688 --reload
pause
