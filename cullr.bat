@echo off
REM Launch cullr on Windows. Passes through any extra flags.
setlocal
cd /d "%~dp0"
python -m cullr --open %*
endlocal
