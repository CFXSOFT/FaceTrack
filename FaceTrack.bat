@echo off
title FaceTrack - SENATI
cd /d "%~dp0web"

echo ============================================
echo   FaceTrack - Sistema de Asistencia SENATI
echo ============================================
echo.
echo Iniciando el sistema, espera unos segundos...
echo NO CIERRES esta ventana mientras uses FaceTrack.
echo.

start "" cmd /c "timeout /t 15 /nobreak >nul && start http://localhost:5000"

py app.py

echo.
echo El sistema se detuvo. Presiona una tecla para cerrar esta ventana.
pause >nul