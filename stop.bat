@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Останавливаю бота...
docker compose down
echo.
echo Бот остановлен. Запустить снова — start.bat
pause
