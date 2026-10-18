@echo off
chcp 65001 >nul
cd /d "%~dp0"
"app\CSGOJDeploy.exe"
if errorlevel 1 pause
