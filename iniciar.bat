@echo off
title Gestor de Facturas Electronicas
cd /d "%~dp0"

:: Eliminar cache de Python
if exist __pycache__ rmdir /s /q __pycache__

:: El flag -B evita que Python genere archivos .pyc (sin cache = siempre carga la version actual)
python -B main.py

if %errorlevel% neq 0 (
    echo.
    echo Error al iniciar la aplicacion.
    echo Asegurese de tener Python instalado y en el PATH.
    pause
)
