@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo Сборка KamchatkaRadioBot.exe (нужен установленный Python 3.10+)...

if not exist ".build\venv\Scripts\python.exe" (
  py -3 -m venv ".build\venv"
  if errorlevel 1 goto :fail
)
".build\venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt pyinstaller imageio-ffmpeg
if errorlevel 1 goto :fail
".build\venv\Scripts\python.exe" tools\build_exe.py
if errorlevel 1 goto :fail

echo.
echo Готово: KamchatkaRadioBot.exe лежит в папке бота.
pause
exit /b 0

:fail
echo.
echo [!] Сборка не удалась — смотрите сообщения выше.
pause
exit /b 1
