@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if exist ".venv-dashboard\Scripts\python.exe" goto install
py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 goto python_missing
echo 初回準備：Python環境を作成しています。
py -3.12 -m venv ".venv-dashboard"
if errorlevel 1 goto failed
:install
".venv-dashboard\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-windows.txt
if errorlevel 1 goto failed
".venv-dashboard\Scripts\python.exe" scripts\start_dashboard.py
if errorlevel 1 goto failed
goto end
:python_missing
echo Python 3.12が必要です。docs\windows-dashboard.md の手順でインストールしてください。
pause
exit /b 1
:failed
echo 起動できませんでした。ネット接続と docs\windows-dashboard.md を確認してください。
pause
exit /b 1
:end
endlocal
