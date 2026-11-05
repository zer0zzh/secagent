@echo off
chcp 65001 >nul
pushd "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [x] python not found in PATH
  pause
  exit /b 1
)

set "OLLAMA_EXE=E:\Ollama\ollama.exe"
if not exist "%OLLAMA_EXE%" set "OLLAMA_EXE=ollama"

curl -s -m 2 http://127.0.0.1:11434/api/version >nul 2>nul
if errorlevel 1 (
  echo [*] starting ollama server ...
  start "ollama" /min "%OLLAMA_EXE%" serve
  ping -n 6 127.0.0.1 >nul
) else (
  echo [*] ollama already running
)

echo.
echo [*] probe -^> ollama
python -m agent.cli probe
echo.
echo [*] done, press any key to close.
pause >nul
popd
