@echo off
chcp 65001 >nul
pushd "%~dp0"

if "%GITHUB_TOKEN%"=="" (
  echo [x] GITHUB_TOKEN is not set.
  echo     Generate a classic PAT with repo scope, then:
  echo     set GITHUB_TOKEN=YOUR_PAT_HERE
  pause
  exit /b 1
)

netstat -ano | findstr ":18888" | findstr LISTENING >nul
if errorlevel 1 (
  echo [*] starting github relay ...
  start "github-relay" /min python "%~dp0..\github_relay.py"
  ping -n 3 127.0.0.1 >nul
) else (
  echo [*] relay already running
)

echo [*] pushing main -^> origin/main ...
git -c http.proxy=http://127.0.0.1:18888 -c credential.helper= push "https://zer0zzh:%GITHUB_TOKEN%@github.com/zer0zzh/secagent.git" main:main
if errorlevel 1 (
  echo [x] push failed, see message above
  pause
  exit /b 1
)

echo.
echo [*] verifying ...
git -c http.proxy=http://127.0.0.1:18888 ls-remote origin
echo [*] done, press any key to close.
pause >nul
popd
