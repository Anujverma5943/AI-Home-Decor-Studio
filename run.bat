@echo off
title Home Decor and Construction Planning Studio
cd /d %~dp0
where python >nul 2>nul || (echo Python 3.10+ is not installed. Install it from https://www.python.org/downloads/ and tick "Add Python to PATH", then run this file again. & pause & exit /b 1)
if not exist .venv ( echo First run: setting up, please wait... & python -m venv .venv )
call .venv\Scripts\activate
pip install -q -r requirements.txt || ( echo Install failed. Check your internet connection. & pause & exit /b 1 )
if not exist .env copy .env.example .env >nul
cd backend
start "" http://127.0.0.1:8000
echo Studio is running at http://127.0.0.1:8000  (close this window to stop)
uvicorn main:app --port 8000
pause
