@echo off
REM Wrapper to run the hourly pipeline task via PowerShell
setlocal

set SCRIPT_PATH=C:\Monica_program\edu_news_pipeline\scripts\run_pipeline_hourly.ps1
set PYTHON_PATH=C:\Users\huanghc\AppData\Local\Programs\Python\Python313\python.exe

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_PATH%" -Python "%PYTHON_PATH%"

endlocal
