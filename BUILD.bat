@echo off
title Loom - gerar instalador
echo.
echo   Gerando o instalador do Loom.
echo   O resultado sai em dist\Loom-Setup.exe
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build.ps1"
pause
