"""Собирает KamchatkaRadioBot.exe: бот + ffmpeg + плейлисты в одном файле."""

import compileall
import shutil
import sys
from pathlib import Path

import imageio_ffmpeg
import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".build"


def main() -> None:
    # PyInstaller молча пропускает модули с синтаксическими ошибками — проверяем заранее.
    if not compileall.compile_dir(ROOT / "bot", quiet=1, force=True):
        sys.exit("В коде бота есть ошибка — сборка остановлена.")

    ffmpeg_dir = WORK / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), ffmpeg_dir / "ffmpeg.exe")

    PyInstaller.__main__.run([
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name", "KamchatkaRadioBot",
        "--paths", str(ROOT),
        "--collect-data", "discord",  # libopus для Windows лежит внутри discord.py
        "--hidden-import", "_cffi_backend",  # нужен PyNaCl, но сборщик сам его не находит
        "--add-binary", f"{ffmpeg_dir / 'ffmpeg.exe'}{';' if sys.platform == 'win32' else ':'}.",
        "--add-data", f"{ROOT / 'playlists'}{';' if sys.platform == 'win32' else ':'}playlists",
        # Шапка пульта и пиксельные эмодзи (бот сам загружает их в Discord при запуске).
        "--add-data", f"{ROOT / 'bot' / 'assets'}{';' if sys.platform == 'win32' else ':'}bot/assets",
        # Куда положить exe: по умолчанию в папку бота, или в указанную (если старый exe сейчас запущен).
        "--distpath", sys.argv[1] if len(sys.argv) > 1 else str(ROOT),
        "--workpath", str(WORK / "work"),
        "--specpath", str(WORK),
        str(ROOT / "tools" / "exe_entry.py"),
    ])


if __name__ == "__main__":
    main()
