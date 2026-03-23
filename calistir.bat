@echo off
cd /d "%~dp0"
title Binance Teknik Analiz
python app.py
if errorlevel 1 (
  echo.
  echo Hata olustu veya Python bulunamadi.
  echo Kurulum: https://www.python.org - "Add Python to PATH" isaretli olsun.
  echo Sonra: pip install -r requirements.txt
  pause
)
