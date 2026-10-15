@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Журнал радио-бота
echo Журнал бота. Закройте окно, чтобы выйти — бот продолжит работать.
echo.
docker compose logs -f --tail 100
