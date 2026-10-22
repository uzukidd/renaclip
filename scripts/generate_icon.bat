@echo off
setlocal
python "%~dp0generate_icon.py" %*
exit /b %ERRORLEVEL%
