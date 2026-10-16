@echo off
setlocal
pushd "%~dp0.."
python -m interfaces.material_preview
set "PREVIEW_RESULT=%ERRORLEVEL%"
popd
exit /b %PREVIEW_RESULT%
