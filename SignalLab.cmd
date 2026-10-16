@echo off
setlocal
cd /d "%~dp0"
if exist "dist\SignalLab\SignalLab.exe" (
    start "" "dist\SignalLab\SignalLab.exe"
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "desktop.py"
    exit /b 0
)
echo Run setup first: python -m pip install -r requirements-native.txt
python desktop.py
if errorlevel 1 pause
