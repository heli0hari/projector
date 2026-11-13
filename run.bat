@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Setting up ProjMap - first run only...
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python run.py %*
if errorlevel 1 pause
