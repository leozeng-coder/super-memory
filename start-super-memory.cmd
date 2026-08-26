@echo off
chcp 65001 >nul
title Super Memory
node "%~dp0start-web.mjs"
if errorlevel 1 (
  echo.
  echo Super Memory 启动失败，请查看上方错误。
  pause
)
