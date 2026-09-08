@echo off
title DBX Term - gerar instalador
echo.
echo   Gerando o instalador do DBX Term.
echo   O resultado sai em dist\DbxTerm-Setup.exe
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build.ps1"
pause
