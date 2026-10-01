@echo off
title CSA - Ingresos y Gastos
cd /d "%~dp0"
where py >nul 2>nul && (py iniciar.py & goto fin)
where python >nul 2>nul && (python iniciar.py & goto fin)
echo.
echo No se encontro Python. Instalalo desde https://www.python.org/downloads/
echo (tilda "Add Python to PATH" durante la instalacion) y volve a abrir este archivo.
:fin
echo.
pause
