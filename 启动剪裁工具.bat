@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "EXE=%~dp0dist\TaikoNijiiroDondaEx曲谱开头剪裁.exe"
set "SCRIPT=%~dp0tja_ogg_measure_trimmer.py"

if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)

if exist "%SCRIPT%" (
  py -3 "%SCRIPT%"
  exit /b %errorlevel%
)

echo [错误] 找不到程序文件。
pause
exit /b 1
