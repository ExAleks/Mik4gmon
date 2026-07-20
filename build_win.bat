@echo off
chcp 65001 >nul
title Building %APP_NAME% v%VERSION%

set APP_NAME=Mik4gmon
set VERSION=0.0.1
set MAIN_SCRIPT=mikrotik_monitor.py
set ICON=icon.ico
set DIST_DIR=dist

if not exist "%ICON%" (
    echo [WARN] %ICON% not found, building without icon
    set ICON=
)

echo Building %APP_NAME% v%VERSION% for Windows...
echo.

if "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
    set ARCH=ARM64
) else if "%PROCESSOR_ARCHITECTURE%"=="AMD64" (
    set ARCH=64
) else (
    set ARCH=32
)
echo Detected architecture: %ARCH%-bit
echo.

set PYINSTALLER_OPTS=--onefile --windowed --name "%APP_NAME%"

if not "%ICON%"=="" (
    set PYINSTALLER_OPTS=%PYINSTALLER_OPTS% --icon "%ICON%"
)

echo Running PyInstaller...
echo.

pyinstaller %PYINSTALLER_OPTS% "%MAIN_SCRIPT%"

if errorlevel 1 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo.
echo Build completed successfully!
echo.
echo Output: %DIST_DIR%\%APP_NAME%.exe
echo.

pause
