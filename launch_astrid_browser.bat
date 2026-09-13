@echo off
title Astrid Interactive Chrome Launcher
echo ====================================================================
echo Starting Astrid Interactive Google Chrome Desktop Window
echo Port: 9222
echo Safe Mode: Will NOT close any of your existing Chrome tabs!
echo ====================================================================

set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_PATH%" set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

set "PROFILE_DIR=%LOCALAPPDATA%\Google\Chrome\AstridProfile"

start "" "%CHROME_PATH%" --remote-debugging-port=9222 --user-data-dir="%PROFILE_DIR%" --no-first-run --no-default-browser-check --disable-blink-features=AutomationControlled

echo.
echo ====================================================================
echo Astrid Chrome window has launched visibly on your desktop!
echo Any logins or cookies you save in this window will stay forever.
echo Astrid AI will automatically detect and control this visible window!
echo ====================================================================
