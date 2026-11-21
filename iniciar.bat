@echo off
setlocal EnableExtensions
cd /d "D:\ClipRush_Pro_V2"

REM Add FFmpeg from ClipRush_Universal if available
if exist "D:\ClipRush_Universal\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe" (
    set "PATH=D:\ClipRush_Universal\ffmpeg-9.0.2-full_build\bin;%PATH%"
)

REM Free port 8765 if occupied (supports PT-BR ESCUTANDO and EN LISTENING)
for /f "tokens=5" %%a in ('netstat -ano -p TCP ^| findstr ":8765 " ^| findstr /I "LISTENING ESCUTANDO"') do (
    if not "%%a"=="0" taskkill /F /PID %%a >nul 2>&1
)

REM Detect local .venv Python
set "PY_EXE=D:\ClipRush_Pro_V2\.venv\Scripts\pythonw.exe"
if not exist "%PY_EXE%" set "PY_EXE=D:\ClipRush_Pro_V2\.venv\Scripts\python.exe"
if not exist "%PY_EXE%" set "PY_EXE=pythonw.exe"

start "" "%PY_EXE%" "D:\ClipRush_Pro_V2\tray_launcher.py"
exit /b 0
