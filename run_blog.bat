@echo off
cd /d C:\premier-blog
set LOGFILE=C:\premier-blog\log\blog_%date:~0,4%%date:~5,2%%date:~8,2%.log
C:\premier-blog\venv\Scripts\python.exe C:\premier-blog\main.py >> "%LOGFILE%" 2>&1
