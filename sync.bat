@echo off
chcp 65001 >nul
pushd "%~dp0"

if "%GITHUB_TOKEN%"=="" (
  echo [x] GITHUB_TOKEN is not set.
  echo     set GITHUB_TOKEN=YOUR_PAT_HERE
  pause
  exit /b 1
)

echo [*] syncing local main tree to GitHub via Git Data API ...
python "%~dp0..\github_push_api.py" --sync
if errorlevel 1 (
  echo [x] sync failed
  pause
  exit /b 1
)
echo [*] done, press any key to close.
pause >nul
popd
