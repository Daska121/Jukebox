@echo off
setlocal

REM Always go to the folder of this BAT file
cd /d "%~dp0"

REM Activate virtual environment
call "venv\Scripts\activate.bat"

REM Run the bot
python "main.py"
