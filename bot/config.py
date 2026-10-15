"""Настройки бота: файл .env рядом с программой или переменные окружения (в Docker)."""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)  # запущен как KamchatkaRadioBot.exe
# Папка, где лежат .env, playlists и data: рядом с .exe или корень проекта.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
# Файлы, упакованные внутрь .exe (ffmpeg, плейлисты по умолчанию).
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
# Картинки пульта: шапка и пиксельные эмодзи (внутри .exe — в его распакованной копии).
ASSETS_DIR = BUNDLE_DIR / "bot" / "assets"


@dataclass(frozen=True)
class Config:
    token: str
    playlist: Path
    volume: float  # 0.01–1.0
    auto_leave_seconds: int  # 0 — никогда не выходить из пустого канала
    data_dir: Path  # куда сохранять настройки серверов
    ffmpeg: str  # путь к ffmpeg


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):  # Блокнот иногда сохраняет в UTF-16
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def load_env_file() -> None:
    """Читает .env рядом с программой. Переменные, уже заданные в окружении, не перезаписываются."""
    # .env.txt — если Блокнот при сохранении дописал расширение.
    for name in (".env", ".env.txt"):
        path = APP_DIR / name
        if not path.is_file():
            continue
        for line in _read_text(path).splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
        return


def _playlists_dir() -> Path:
    local = APP_DIR / "playlists"
    return local if local.is_dir() else BUNDLE_DIR / "playlists"


def find_ffmpeg() -> str | None:
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidates = [os.getenv("FFMPEG_PATH", "").strip(), str(APP_DIR / exe), str(BUNDLE_DIR / exe)]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("ffmpeg")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"Ошибка в .env: {name} должно быть числом, а сейчас там «{raw}»")


def _check_token_shape(token: str) -> None:
    """Ловит частую ошибку: в .env вставили не токен бота, а другой ключ со страницы приложения."""
    if token.count(".") == 2:
        return  # токен бота выглядит как «MTI3…ABC.Gx1y2z.abc…xyz» — три части через точку
    if re.fullmatch(r"[0-9a-f]{64}", token):
        what = "«Public Key» (публичный ключ) со страницы «Общая информация»"
    elif re.fullmatch(r"\d{17,20}", token):
        what = "ID приложения"
    elif re.fullmatch(r"[A-Za-z0-9_\-]{32}", token):
        what = "«Client Secret» со страницы OAuth2"
    else:
        what = "что-то другое"
    sys.exit(
        f"В .env вместо токена бота вставлен {what}. Токен находится на вкладке «Bot» (Бот): "
        "нажмите «Reset Token» (Сбросить токен) и скопируйте его. Токен бота длинный "
        "и состоит из трёх частей через точку. Подробнее — README.md, шаг 1."
    )


def load_config() -> Config:
    load_env_file()

    # Принимаем и DISCORD_TOKEN, и просто TOKEN.
    token = (os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN") or "").strip().strip("\"'")
    if not token or token == "PASTE_YOUR_TOKEN_HERE":
        sys.exit(
            f"Не задан токен бота. Создайте в папке {APP_DIR} файл .env со строкой TOKEN=ваш_токен "
            "(как получить токен — смотрите README.md, шаг 1)."
        )
    _check_token_shape(token)

    playlists_dir = _playlists_dir()
    playlist = Path(os.getenv("PLAYLIST", "").strip() or "kamchatka.pls")
    if not playlist.is_absolute():
        playlist = playlists_dir / playlist.name
    if not playlist.is_file():
        available = ", ".join(sorted(p.name for p in playlists_dir.glob("*") if p.is_file())) or "пусто"
        sys.exit(f"Плейлист {playlist.name} не найден в папке playlists. Там есть: {available}")

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        sys.exit("Не найден ffmpeg — без него бот не сможет играть радио.")

    volume = min(max(_int("DEFAULT_VOLUME", 50), 1), 100) / 100
    auto_leave = max(_int("AUTO_LEAVE_SECONDS", 300), 0)
    data_dir = Path(os.getenv("DATA_DIR", "").strip() or APP_DIR / "data")
    return Config(
        token=token,
        playlist=playlist,
        volume=volume,
        auto_leave_seconds=auto_leave,
        data_dir=data_dir,
        ffmpeg=ffmpeg,
    )
