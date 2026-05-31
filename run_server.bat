@echo off
setlocal
cd /d "%~dp0"
"D:\zzrj\anaconda\envs\math\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
