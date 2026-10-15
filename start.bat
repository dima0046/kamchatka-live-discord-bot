@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Радио-бот

where docker >nul 2>nul
if errorlevel 1 (
  echo [!] Docker не найден. Установите Docker Desktop: https://www.docker.com/products/docker-desktop/
  echo     Подробности — в README.md, раздел «Запуск через Docker».
  pause
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo [!] Docker Desktop не запущен.
  echo     Запустите Docker Desktop из меню «Пуск», подождите, пока он загрузится, и снова запустите start.bat.
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Создан файл настроек .env — сейчас он откроется в Блокноте.
  echo Вставьте токен бота вместо PASTE_YOUR_TOKEN_HERE, сохраните файл Ctrl+S и закройте Блокнот.
  echo.
  start /wait notepad ".env"
)

findstr /c:"PASTE_YOUR_TOKEN_HERE" ".env" >nul
if not errorlevel 1 (
  echo [!] В файле .env не указан токен бота.
  echo     Откройте .env в Блокноте, вставьте токен вместо PASTE_YOUR_TOKEN_HERE, сохраните и снова запустите start.bat.
  pause
  exit /b 1
)

echo Собираю и запускаю бота (в первый раз это займёт пару минут)...
docker compose up -d --build --force-recreate
if errorlevel 1 (
  echo.
  echo [!] Не удалось запустить бота. Посмотрите сообщения выше.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  Бот запущен и работает в фоне. Ниже — его журнал.
echo  Ссылка для добавления бота на сервер появится через несколько секунд.
echo  Это окно можно закрыть — бот продолжит работать.
echo ============================================================
echo.
docker compose logs -f --tail 30
