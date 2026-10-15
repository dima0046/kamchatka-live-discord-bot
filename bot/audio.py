"""Буфер звука между радиопотоком и Discord.

discord.py отправляет звук пакетами по 20 мс по строгому графику. Если чтение потока задержалось
(запуск ffmpeg, подвисание сети), он догоняет график, отправляя накопившиеся пакеты пачкой.
Discord у слушателей на пачки отвечает ускорением звука, а на провалы — замедлением.
Буфер держит пару секунд звука наготове: чтение всегда мгновенное, и пакеты уходят ровно.
"""

from __future__ import annotations

import collections
import logging
import threading

import discord
from discord.opus import Encoder

log = logging.getLogger("radio.audio")

FRAMES_PER_SECOND = 50  # один кадр — 20 мс
SILENCE = b"\x00" * Encoder.FRAME_SIZE


class BufferedPCMAudio(discord.AudioSource):
    def __init__(
        self,
        source: discord.AudioSource,
        *,
        prebuffer_seconds: float = 2.0,
        rebuffer_seconds: float = 1.0,
        max_seconds: float = 10.0,
    ) -> None:
        self._source = source
        self._frames: collections.deque[bytes] = collections.deque()
        self._cond = threading.Condition()
        self._prebuffer = int(prebuffer_seconds * FRAMES_PER_SECOND)
        self._rebuffer = int(rebuffer_seconds * FRAMES_PER_SECOND)
        self._max = int(max_seconds * FRAMES_PER_SECOND)
        self._eof = False
        self._closed = False
        self._rebuffering = False
        self.ready = threading.Event()  # набрался запас для начала воспроизведения (или поток закончился)
        self._thread = threading.Thread(target=self._fill, name="radio-buffer", daemon=True)
        self._thread.start()

    def _fill(self) -> None:
        try:
            while not self._closed:
                data = self._source.read()
                with self._cond:
                    if not data:
                        break
                    while len(self._frames) >= self._max and not self._closed:
                        self._cond.wait(0.1)
                    self._frames.append(data)
                    if len(self._frames) >= self._prebuffer:
                        self.ready.set()
        except Exception:
            if not self._closed:
                log.exception("Ошибка при чтении радиопотока")
        finally:
            with self._cond:
                self._eof = True
            self.ready.set()

    def read(self) -> bytes:
        with self._cond:
            if self._rebuffering and len(self._frames) < self._rebuffer and not self._eof:
                return SILENCE
            if self._frames:
                if self._rebuffering:
                    log.info("Связь с радио восстановилась, звук идёт дальше")
                    self._rebuffering = False
                frame = self._frames.popleft()
                self._cond.notify()
                return frame
            if self._eof:
                return b""  # ffmpeg завершился — плеер остановится, бот переподключится
            # Запас кончился (долгое подвисание связи): отдаём тишину, а не ждём,
            # чтобы не сбить ровный график отправки. Продолжим, когда снова наберётся запас.
            self._rebuffering = True
        log.warning("Радио не присылает звук — жду, пока наберётся запас")
        return SILENCE

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._closed = True
        with self._cond:
            self._cond.notify_all()
        self._source.cleanup()
