@echo off
chcp 65001 >nul
pushd "%~dp0"

curl -s -m 2 http://127.0.0.1:11434/api/version >nul 2>nul
if errorlevel 1 (
  echo [*] starting ollama ...
  start "ollama" /min "E:\Ollama\ollama.exe" serve
  ping -n 6 127.0.0.1 >nul
)

curl -s -m 2 http://127.0.0.1:8080/ >nul 2>nul
if errorlevel 1 (
  echo [*] starting lab target on 127.0.0.1:8080 ...
  start "lab-target" /min cmd /c "python -m http.server 8080 --bind 127.0.0.1 --directory lab\www"
  ping -n 3 127.0.0.1 >nul
) else (
  echo [*] target already listening on 8080
)

echo.
python -m agent.run "对 http://127.0.0.1:8080 做一次 HTTP 指纹识别并给出结论"
echo.
echo [*] press any key to close (the lab target window stays open)
pause >nul
popd
