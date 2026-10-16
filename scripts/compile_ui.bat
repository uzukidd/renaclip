@echo off
rem Main UI and service now ship as one Qt application.
call "%~dp0compile_app.bat"
exit /b %ERRORLEVEL%
