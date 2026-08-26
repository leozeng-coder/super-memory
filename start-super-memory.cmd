@echo off
node "%~dp0start-web.mjs"
if errorlevel 1 (
  echo.
  echo Super Memory 启动失败，请查看上方错误。
  pause
)
