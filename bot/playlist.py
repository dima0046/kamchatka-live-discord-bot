"""Чтение плейлиста: PLS, M3U/M3U8 и XSPF."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Discord разрешает максимум 25 вариантов в слэш-команде и 25 кнопок в сообщении
# (одна из кнопок занята «Стоп»).
MAX_STATIONS = 20

EMOJI_BY_KEYWORD = {
    "dance": "💃",
    "chill": "🌴",
    "house": "🎛️",
    "rock": "🎸",
    "jazz": "🎷",
    "lounge": "🍸",
    "trance": "🌀",
    "techno": "🤖",
    "pop": "🎤",
}
DEFAULT_EMOJI = "📻"


@dataclass(frozen=True)
class Station:
    title: str  # полное название из плейлиста, например «Kamchatka Live Dance»
    name: str  # короткое название для кнопок, например «Dance»
    url: str
    emoji: str
    genre: str = ""  # ключевое слово из EMOJI_BY_KEYWORD: по нему находится пиксельный эмодзи бота

    @property
    def mount(self) -> str:
        return urlsplit(self.url).path


@dataclass(frozen=True)
class Playlist:
    name: str  # общее название радио, например «Kamchatka Live»
    stations: list[Station]


def _title_from_url(url: str) -> str:
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail.replace("_", " ").replace("-", " ").title() or url


def _parse_pls(text: str) -> list[tuple[str, str]]:
    files: dict[int, str] = {}
    titles: dict[int, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*(File|Title)(\d+)\s*=\s*(.*?)\s*$", line, re.IGNORECASE)
        if not m:
            continue
        key, num, value = m.group(1).lower(), int(m.group(2)), m.group(3)
        (files if key == "file" else titles)[num] = value
    return [(titles.get(n) or _title_from_url(url), url) for n, url in sorted(files.items())]


def _parse_m3u(text: str) -> list[tuple[str, str]]:
    entries = []
    pending_title = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.upper().startswith("#EXTINF"):
            pending_title = line.split(",", 1)[1].strip() if "," in line else None
        elif not line.startswith("#"):
            entries.append((pending_title or _title_from_url(line), line))
            pending_title = None
    return entries


def _parse_xspf(text: str) -> list[tuple[str, str]]:
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    entries = []
    for track in ET.fromstring(text).iter():
        if local(track.tag) != "track":
            continue
        fields = {local(child.tag): (child.text or "").strip() for child in track}
        url = fields.get("location")
        if url:
            entries.append((fields.get("title") or _title_from_url(url), url))
    return entries


PARSERS = {
    ".pls": _parse_pls,
    ".m3u": _parse_m3u,
    ".m3u8": _parse_m3u,
    ".xspf": _parse_xspf,
}


def _genre_for(title: str) -> tuple[str, str]:
    """Жанр потока по названию и обычный эмодзи для него."""
    lowered = title.lower()
    for keyword, emoji in EMOJI_BY_KEYWORD.items():
        if keyword in lowered:
            return keyword, emoji
    return "", DEFAULT_EMOJI


def _common_prefix_words(titles: list[str]) -> list[str]:
    split = [t.split() for t in titles]
    prefix = []
    for words in zip(*split):
        if len({w.lower() for w in words}) != 1:
            break
        prefix.append(words[0])
    # У каждой станции должно остаться хоть одно слово после отрезания префикса.
    while prefix and any(len(s) <= len(prefix) for s in split):
        prefix.pop()
    return prefix


def load_playlist(path: Path) -> Playlist:
    parser = PARSERS.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"Неизвестный формат плейлиста: {path.name} (поддерживаются .pls, .m3u, .m3u8, .xspf)")

    entries = [(t, u) for t, u in parser(path.read_text(encoding="utf-8-sig")) if u.startswith(("http://", "https://"))]
    if not entries:
        raise ValueError(f"В плейлисте {path.name} не найдено ни одной ссылки на поток")
    entries = entries[:MAX_STATIONS]

    titles = [t for t, _ in entries]
    prefix = _common_prefix_words(titles) if len(titles) > 1 else []
    stations = []
    for title, url in entries:
        genre, emoji = _genre_for(title)
        stations.append(
            Station(title=title, name=" ".join(title.split()[len(prefix):])[:80], url=url, emoji=emoji, genre=genre)
        )
    name = " ".join(prefix) or path.stem.replace("_", " ").title()
    return Playlist(name=name, stations=stations)
