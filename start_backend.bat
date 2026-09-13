@echo off
title Astrid Backend Engine
cd /d "%~dp0backend"
echo ====================================================================
echo Starting Astrid Backend Engine in Interactive Desktop Console
echo URL: http://localhost:8080
echo WebSocket: ws://localhost:8080/ws/task
echo ====================================================================
.\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8080
pause
