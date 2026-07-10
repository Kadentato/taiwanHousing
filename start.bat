@echo off
cd /d "%~dp0"
echo ============================================================
echo   Taiwan Housing Explorer
echo   Serving webApp at http://localhost:8777
echo   A browser tab will open in a moment.
echo   Close the "Taiwan Housing server" window to stop it.
echo ============================================================
start "Taiwan Housing server" python -m http.server 8777 --directory webApp
timeout /t 2 /nobreak >nul
start "" http://localhost:8777
