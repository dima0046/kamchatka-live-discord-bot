"""Что сейчас играет: периодически спрашиваем у сервера Icecast названия треков."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

import aiohttp

from .playlist import Station

log = logging.getLogger("radio.nowplaying")


def _status_url(station: Station) -> str:
    parts = urlsplit(station.url)
    return f"{parts.scheme}://{parts.netloc}/status-json.xsl"


class NowPlaying:
    def __init__(self, stations: list[Station]) -> None:
        self.stations = stations
        self.titles: dict[str, str] = {}  # url потока -> «Исполнитель - Трек»
        self._failed: set[str] = set()

    def get(self, station: Station) -> str | None:
        return self.titles.get(station.url)

    async def refresh(self, session: aiohttp.ClientSession) -> bool:
        """Обновляет названия треков. Возвращает True, если что-то поменялось."""
        by_server: dict[str, list[Station]] = {}
        for station in self.stations:
            by_server.setdefault(_status_url(station), []).append(station)

        titles: dict[str, str] = {}
        for status_url, stations in by_server.items():
            try:
                async with session.get(status_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    resp.raise_for_status()
                    # Icecast иногда отдаёт управляющие символы внутри названий — strict=False их пропускает.
                    data = json.loads(await resp.text(errors="replace"), strict=False)
            except Exception as exc:
                if status_url not in self._failed:
                    log.info("Не удалось получить названия треков с %s: %s", status_url, exc)
                    self._failed.add(status_url)
                continue
            self._failed.discard(status_url)

            sources = data.get("icestats", {}).get("source", [])
            if isinstance(sources, dict):  # Icecast отдаёт объект, если поток один
                sources = [sources]
            by_mount = {}
            for source in sources:
                title = (source.get("title") or source.get("yp_currently_playing") or "").strip()
                if title:
                    by_mount[urlsplit(source.get("listenurl", "")).path] = title
            for station in stations:
                if station.mount in by_mount:
                    titles[station.url] = by_mount[station.mount]

        changed = titles != self.titles
        self.titles = titles
        return changed
