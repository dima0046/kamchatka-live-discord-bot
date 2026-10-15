"""Пиксельные эмодзи пульта. Бот сам загружает их в своё приложение Discord из папки bot/assets/emoji.

Эмодзи приложения работают на любом сервере без Nitro. Если загрузить их не получилось,
на пульте остаются обычные эмодзи — бот работает как прежде.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

import discord

from .config import ASSETS_DIR

log = logging.getLogger("radio.emoji")

EMOJI_DIR = ASSETS_DIR / "emoji"


class Emojis:
    def __init__(self, manifest: Path) -> None:
        # Отпечатки загруженных картинок: если файл в папке поменялся, эмодзи в Discord заменяется.
        self.manifest = manifest
        self.loaded: dict[str, discord.Emoji] = {}

    def get(self, name: str, fallback: str) -> discord.Emoji | str:
        return self.loaded.get(name) or fallback

    async def sync(self, client: discord.Client) -> None:
        files = {p.stem: p for p in sorted(EMOJI_DIR.glob("*")) if p.suffix.lower() in (".png", ".gif")}
        if not files:
            return
        try:
            existing = {e.name: e for e in await client.fetch_application_emojis()}
        except discord.HTTPException as exc:
            log.warning("Не удалось получить эмодзи бота, на пульте будут обычные: %s", exc)
            return

        uploaded = self._read_manifest()
        for name, path in files.items():
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:16]
            emoji = existing.get(name)
            if emoji is None or uploaded.get(name) != digest:
                try:
                    if emoji is not None:
                        await emoji.delete()  # картинку обновили — заменяем эмодзи
                    emoji = await client.create_application_emoji(name=name, image=data)
                except discord.HTTPException as exc:
                    log.warning("Не удалось загрузить эмодзи :%s:, вместо него будет обычный: %s", name, exc)
                    continue
                uploaded[name] = digest
                log.info("Загрузил эмодзи :%s:", name)
            self.loaded[name] = emoji
        self._write_manifest(uploaded)

    def _read_manifest(self) -> dict[str, str]:
        try:
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_manifest(self, uploaded: dict[str, str]) -> None:
        try:
            self.manifest.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.manifest.with_suffix(".tmp")
            tmp.write_text(json.dumps(uploaded, indent=2), encoding="utf-8")
            os.replace(tmp, self.manifest)
        except OSError as exc:
            log.warning("Не удалось сохранить список эмодзи в %s: %s", self.manifest, exc)
