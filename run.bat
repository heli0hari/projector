@echo off
setlocal
cd /d "%~dp0"

if exist .venv\.ready goto run

echo Setting up ProjMap (first run only)...
if exist .venv rmdir /s /q .venv
py -3.13 -m venv .venv 2>nul || py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || python -m venv .venv
if not exist .venv\Scripts\python.exe (
  echo Could not create a Python environment. Install Python 3.13 from python.org.
  pause & exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installing the core packages failed.
  pause & exit /b 1
)
.venv\Scripts\python -m pip install SpoutGL
if errorlevel 1 (
  echo.
  echo WARNING: SpoutGL needs Python 3.11 - 3.13. ProjMap will run, but without Spout input.
  echo Install Python 3.13 from python.org, delete the .venv folder and run this again.
  echo.
)
echo ok> .venv\.ready

:run
.venv\Scripts\python run.py %*
if errorlevel 1 pause
