"""Настройки серверов, которые переживают перезапуск бота: где стоит пульт и какая громкость."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("radio.storage")


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, dict[str, Any]] = {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Не удалось прочитать сохранённые настройки %s: %s", path, exc)

    def guild(self, guild_id: int) -> dict[str, Any]:
        return self.data.get(str(guild_id), {})

    def update(self, guild_id: int, **values: Any) -> None:
        """Сохраняет значения для сервера; значение None удаляет ключ."""
        entry = self.data.setdefault(str(guild_id), {})
        for key, value in values.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Не удалось сохранить настройки в %s: %s", self.path, exc)
