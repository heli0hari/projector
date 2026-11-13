@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Run run.bat once first to set up the environment.
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --windowed --name ProjMap --collect-all imgui_bundle --collect-all SpoutGL --collect-all glfw run.py
echo Built: dist\ProjMap\ProjMap.exe
pause
