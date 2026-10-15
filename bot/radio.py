"""Discord-бот, который играет потоки интернет-радио из плейлиста в голосовом канале."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes.util
import logging
import logging.handlers
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import discord
from discord import app_commands

from .audio import BufferedPCMAudio
from .config import ASSETS_DIR, FROZEN, Config, load_config
from .emojis import Emojis
from .nowplaying import NowPlaying
from .playlist import Playlist, Station, load_playlist
from .storage import Storage

log = logging.getLogger("radio")

# ffmpeg сам переподключается при обрывах связи с радиосервером.
FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_at_eof 1 -reconnect_on_network_error 1 "
    "-reconnect_delay_max 10 -rw_timeout 15000000 -user_agent DiscordRadioBot"
)
FFMPEG_OPTIONS = "-vn -loglevel error"
# Сколько максимум ждать, пока наберётся запас звука, прежде чем начать играть.
PREBUFFER_TIMEOUT = 10
POLL_SECONDS = 15
# Если в канале с пультом кто-то пишет, после стольких секунд тишины пульт переносится вниз.
BUMP_SECONDS = 30
VOLUME_STEP_PERCENT = 10
INVITE_PERMISSIONS = discord.Permissions(
    view_channel=True,
    send_messages=True,
    embed_links=True,
    read_message_history=True,
    connect=True,
    speak=True,
)
PANEL_PERMISSIONS = {
    "view_channel": "Просматривать канал",
    "send_messages": "Отправлять сообщения",
    "embed_links": "Встраивать ссылки",
    "read_message_history": "Читать историю сообщений",
}
# Шапка пульта; если файла нет, вместо неё пишется название радио.
PANEL_HEADER = ASSETS_DIR / "panel-header.png"
# Версия вида пульта: пульты старого вида бот при запуске ставит заново.
PANEL_LAYOUT = 2
# Неоновая полоса пульта в цветах логотипа: маджента, когда играет, тёмный ультрафиолет — когда нет.
ACCENT_PLAYING = 0xFF2BD6
ACCENT_IDLE = 0x4B2E83


@dataclass
class GuildState:
    volume: float
    panel: tuple[int, int] | None = None  # постоянный пульт: (id канала, id сообщения)
    station: Station | None = None
    # Растёт при каждом запуске и остановке потока: по нему отбрасываются колбэки от старых потоков.
    generation: int = 0
    started_at: float = 0.0
    retries: int = 0
    leave_task: asyncio.Task | None = None
    bump_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _clean(text: str, limit: int = 100) -> str:
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return discord.utils.escape_markdown(text)


class RadioBot(discord.Client):
    def __init__(self, config: Config, playlist: Playlist) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        intents.guild_messages = True  # чтобы замечать переписку в канале с пультом (текст не читается)
        super().__init__(intents=intents)
        self.config = config
        self.playlist = playlist
        self.stations = playlist.stations
        self.now = NowPlaying(self.stations)
        self.storage = Storage(config.data_dir / "guilds.json")
        self.icons = Emojis(config.data_dir / "emojis.json")
        self.header = PANEL_HEADER if PANEL_HEADER.is_file() else None
        self.tree = app_commands.CommandTree(self)
        self.states: dict[int, GuildState] = {}
        self._synced: set[int] = set()
        self._presence: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._poll_task: asyncio.Task | None = None

    def state(self, guild_id: int) -> GuildState:
        if guild_id not in self.states:
            saved = self.storage.guild(guild_id)
            volume = saved.get("volume")
            panel = saved.get("panel")
            self.states[guild_id] = GuildState(
                volume=volume if isinstance(volume, (int, float)) and 0 < volume <= 1 else self.config.volume,
                panel=(int(panel[0]), int(panel[1])) if isinstance(panel, list) and len(panel) == 2 else None,
            )
        return self.states[guild_id]

    @staticmethod
    def voice(guild: discord.Guild) -> discord.VoiceClient | None:
        vc = guild.voice_client
        return vc if isinstance(vc, discord.VoiceClient) else None

    # ---------- запуск ----------

    async def setup_hook(self) -> None:
        self._session = aiohttp.ClientSession(headers={"User-Agent": "DiscordRadioBot"})
        await self.icons.sync(self)
        # Кнопки пульта продолжают работать и после перезапуска бота.
        self.add_view(self.build_panel(None))
        register_commands(self)
        self._poll_task = asyncio.create_task(self._poll_now_playing())

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
        await super().close()

    async def on_ready(self) -> None:
        assert self.user is not None
        invite = discord.utils.oauth_url(
            self.application_id or self.user.id,
            permissions=INVITE_PERMISSIONS,
            scopes=("bot", "applications.commands"),
        )
        log.info("Бот %s запущен. Серверов: %d", self.user, len(self.guilds))
        log.info("Ссылка, чтобы добавить бота на сервер:\n\n    %s\n", invite)
        if not self.guilds:
            log.info("Бот пока не добавлен ни на один сервер — откройте ссылку выше в браузере.")
        for guild in self.guilds:
            await self._sync_commands(guild)
            if self.state(guild.id).panel is None:
                log.info("На сервере «%s» пульта ещё нет — напишите /radio-setup в нужном текстовом канале.", guild.name)
            elif self.storage.guild(guild.id).get("layout") != PANEL_LAYOUT:
                # Пульт старого вида нельзя переделать правкой сообщения — ставим новый на его место.
                try:
                    if await self._repost_panel(guild):
                        log.info("Пульт на сервере «%s» заменён на новый", guild.name)
                except discord.HTTPException as exc:
                    log.warning("Не удалось заменить пульт на сервере «%s»: %s. Напишите /radio-setup.", guild.name, exc)
            else:
                await self.refresh_panel(guild)
        await self._update_presence()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Меня добавили на сервер «%s». Напишите /radio-setup в канале, где должен быть пульт.", guild.name)
        await self._sync_commands(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._synced.discard(guild.id)
        self.states.pop(guild.id, None)

    async def _sync_commands(self, guild: discord.Guild) -> None:
        # Команды регистрируются отдельно на каждом сервере — так они появляются сразу.
        if guild.id in self._synced:
            return
        self.tree.copy_global_to(guild=guild)
        try:
            await self.tree.sync(guild=guild)
        except discord.HTTPException as exc:
            log.warning("Не удалось зарегистрировать команды на сервере «%s»: %s", guild.name, exc)
            return
        self._synced.add(guild.id)
        log.info("Команды зарегистрированы на сервере «%s»", guild.name)

    # ---------- воспроизведение ----------

    async def play(self, guild: discord.Guild, channel: discord.abc.Connectable, station: Station) -> None:
        state = self.state(guild.id)
        async with state.lock:
            vc = self.voice(guild)
            if vc is not None and not vc.is_connected():
                await vc.disconnect(force=True)
                vc = None
            if vc is None:
                await channel.connect(self_deaf=True, timeout=30)
            elif vc.channel != channel:
                await vc.move_to(channel)
            state.station = station
            state.retries = 0
            await self._start_stream(guild)
        self._check_alone(guild)
        await self._update_presence()

    async def _start_stream(self, guild: discord.Guild) -> None:
        state = self.state(guild.id)
        station = state.station
        if self.voice(guild) is None or station is None:
            return
        state.generation += 1
        generation = state.generation
        buffered = BufferedPCMAudio(
            discord.FFmpegPCMAudio(
                station.url,
                executable=self.config.ffmpeg,
                before_options=FFMPEG_BEFORE_OPTIONS,
                options=FFMPEG_OPTIONS,
            )
        )
        # Пока набирается запас звука, продолжает играть прежний поток — переключение без паузы.
        await asyncio.get_running_loop().run_in_executor(None, buffered.ready.wait, PREBUFFER_TIMEOUT)
        vc = self.voice(guild)
        if vc is None or state.generation != generation or state.station is not station:
            buffered.cleanup()  # пока ждали, поток успели выключить или переключить
            return
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        state.started_at = time.monotonic()
        vc.play(
            # Громкость применяется уже после буфера — поэтому кнопки «−» и «+» срабатывают сразу.
            discord.PCMVolumeTransformer(buffered, volume=state.volume),
            signal_type="music",
            after=lambda error: self._after_stream(guild.id, generation, error),
        )
        log.info("▶ «%s»: включил %s в #%s", guild.name, station.title, vc.channel)

    def _after_stream(self, guild_id: int, generation: int, error: Exception | None) -> None:
        # Вызывается из потока плеера, а не из asyncio.
        if error is not None:
            log.warning("Поток прервался с ошибкой: %s", error)
        if self.is_closed():
            return
        with contextlib.suppress(RuntimeError):  # цикл событий уже закрыт — бот выключается
            asyncio.run_coroutine_threadsafe(self._on_stream_end(guild_id, generation), self.loop)

    async def _on_stream_end(self, guild_id: int, generation: int) -> None:
        state = self.states.get(guild_id)
        guild = self.get_guild(guild_id)
        if state is None or guild is None or state.generation != generation or state.station is None:
            return  # поток остановили или переключили намеренно
        if self.voice(guild) is None:
            return
        if time.monotonic() - state.started_at > 60:
            state.retries = 0
        state.retries += 1
        delay = min(5 * state.retries, 60)
        log.warning("Поток «%s» оборвался, переподключаюсь через %d с", state.station.title, delay)
        await asyncio.sleep(delay)
        async with state.lock:
            if state.generation == generation and state.station is not None:
                await self._start_stream(guild)

    async def stop(self, guild: discord.Guild) -> None:
        state = self.state(guild.id)
        async with state.lock:
            state.station = None
            state.generation += 1
            self._cancel_leave(state)
            vc = self.voice(guild)
            if vc is not None:
                await vc.disconnect(force=True)
        log.info("⏹ «%s»: радио выключено", guild.name)
        await self._update_presence()

    def set_volume(self, guild: discord.Guild, volume: float) -> float:
        volume = round(min(max(volume, 0.01), 1.0), 2)
        self.state(guild.id).volume = volume
        self.storage.update(guild.id, volume=volume)
        vc = self.voice(guild)
        if vc is not None and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = volume
        return volume

    # ---------- выход из пустого канала ----------

    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        guild = member.guild
        if self.user is not None and member.id == self.user.id and after.channel is None:
            # Бота отключили от канала (или он вышел сам).
            state = self.state(guild.id)
            self._cancel_leave(state)
            if state.station is not None:
                log.info("Меня отключили от голосового канала на «%s»", guild.name)
                state.station = None
                state.generation += 1
                await self._update_presence()
                await self.refresh_panel(guild)
            return
        self._check_alone(guild)

    def _count_listeners(self, channel: discord.abc.GuildChannel) -> int:
        count = 0
        for user_id in getattr(channel, "voice_states", {}):
            member = channel.guild.get_member(user_id)
            if (self.user and user_id == self.user.id) or (member is not None and member.bot):
                continue
            count += 1
        return count

    def _check_alone(self, guild: discord.Guild) -> None:
        state = self.state(guild.id)
        vc = self.voice(guild)
        alone = (
            self.config.auto_leave_seconds > 0
            and vc is not None
            and vc.channel is not None
            and self._count_listeners(vc.channel) == 0
        )
        if alone and state.leave_task is None:
            state.leave_task = asyncio.create_task(self._leave_when_alone(guild))
        elif not alone:
            self._cancel_leave(state)

    @staticmethod
    def _cancel_leave(state: GuildState) -> None:
        if state.leave_task is not None:
            state.leave_task.cancel()
            state.leave_task = None

    async def _leave_when_alone(self, guild: discord.Guild) -> None:
        try:
            await asyncio.sleep(self.config.auto_leave_seconds)
        except asyncio.CancelledError:
            return
        state = self.state(guild.id)
        state.leave_task = None
        vc = self.voice(guild)
        if vc is None or vc.channel is None or self._count_listeners(vc.channel) > 0:
            return
        log.info("В голосовом канале никого нет — выключаю радио на «%s»", guild.name)
        await self.stop(guild)
        await self.refresh_panel(guild)

    # ---------- пульт ----------

    def station_emoji(self, station: Station) -> discord.Emoji | str:
        return self.icons.get(f"radio_{station.genre}", station.emoji)

    def build_panel(self, guild: discord.Guild | None, *, buttons: bool = True) -> RadioPanel:
        """Пульт в неоновой рамке. Без сервера — одни кнопки: так они регистрируются при запуске."""
        panel = RadioPanel()
        if guild is None:
            for row in self._button_rows(None):
                panel.add_item(row)
            return panel

        state = self.state(guild.id)
        vc = self.voice(guild)
        station = state.station if vc is not None and vc.channel is not None else None
        eq = self.icons.get("radio_eq", "▶️")
        lines = [] if self.header is not None else [f"## 📻 {self.playlist.name}"]
        if station is not None and vc is not None:
            track = self.now.get(station)
            lines.append(f"### {eq} Сейчас играет: {self.station_emoji(station)} {station.name}")
            if track:
                lines.append(f"{self.icons.get('radio_note', '🎵')} **{_clean(track)}**")
            lines.append(f"{self.icons.get('radio_listen', '🔈')} в канале {vc.channel.mention}")
        else:
            lines.append(f"### {self.icons.get('radio_stop', '⏹')} Сейчас ничего не играет")
            lines.append(
                "Зайдите в голосовой канал и нажмите кнопку с потоком — бот придёт к вам "
                f"{self.icons.get('radio_down', '👇')}"
            )

        frame = discord.ui.Container(accent_colour=ACCENT_PLAYING if station is not None else ACCENT_IDLE)
        if self.header is not None:
            frame.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{self.header.name}")))
        frame.add_item(discord.ui.TextDisplay("\n".join(lines)))
        on_air = [
            f"{eq if st == station else self.station_emoji(st)} **{st.name}** — {_clean(t, 80)}"
            for st in self.stations
            if (t := self.now.get(st))
        ]
        if on_air:
            frame.add_item(discord.ui.Separator())
            frame.add_item(discord.ui.TextDisplay("**В эфире на всех потоках**\n" + "\n".join(on_air)))
        if buttons:
            frame.add_item(discord.ui.Separator())
            for row in self._button_rows(station):
                frame.add_item(row)
        frame.add_item(discord.ui.TextDisplay(f"-# Громкость: {round(state.volume * 100)}%"))
        panel.add_item(frame)
        return panel

    def _button_rows(self, active: Station | None) -> list[discord.ui.ActionRow]:
        """Кнопки потоков по пять в ряд, в последнем ряду — громкость «−», «Стоп» и громкость «+»."""
        rows = [
            discord.ui.ActionRow(
                *(StationButton(self, i, st, st == active) for i, st in enumerate(self.stations) if start <= i < start + 5)
            )
            for start in range(0, len(self.stations), 5)
        ]
        rows.append(
            discord.ui.ActionRow(
                ControlButton(self, "quieter", "−", self.icons.get("radio_quieter", "🔉"), discord.ButtonStyle.secondary),
                ControlButton(self, "stop", "Стоп", self.icons.get("radio_stop", "⏹️"), discord.ButtonStyle.danger),
                ControlButton(self, "louder", "+", self.icons.get("radio_louder", "🔊"), discord.ButtonStyle.secondary),
            )
        )
        return rows

    def header_files(self) -> list[discord.File]:
        # discord.File читается один раз — на каждое сообщение нужен новый.
        return [discord.File(self.header, filename=self.header.name)] if self.header is not None else []

    def _set_panel(self, guild: discord.Guild, panel: tuple[int, int] | None) -> None:
        self.state(guild.id).panel = panel
        self.storage.update(guild.id, panel=list(panel) if panel else None, layout=PANEL_LAYOUT if panel else None)

    async def _post_panel(self, guild: discord.Guild, channel: discord.abc.Messageable) -> discord.Message:
        """Публикует постоянный пульт в канале; предыдущий пульт удаляется."""
        state = self.state(guild.id)
        old = state.panel
        message = await channel.send(view=self.build_panel(guild), files=self.header_files())
        self._set_panel(guild, (message.channel.id, message.id))
        if old is not None and old[1] != message.id:
            with contextlib.suppress(discord.HTTPException):
                await self.get_partial_messageable(old[0], guild_id=guild.id).get_partial_message(old[1]).delete()
        return message

    async def _repost_panel(self, guild: discord.Guild) -> bool:
        """Ставит пульт заново в его канал — в самый низ переписки."""
        state = self.state(guild.id)
        if state.panel is None:
            return False
        channel = guild.get_channel_or_thread(state.panel[0])
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("Канал с пультом на сервере «%s» не найден. Напишите /radio-setup в нужном канале.", guild.name)
            self._set_panel(guild, None)
            return False
        await self._post_panel(guild, channel)
        return True

    async def _edit_panel(self, edit: Callable[..., Awaitable[Any]], guild: discord.Guild) -> None:
        """Перерисовывает пульт. Шапка остаётся во вложениях сообщения — заново её не загружаем."""
        try:
            await edit(view=self.build_panel(guild))
        except discord.HTTPException as exc:
            if exc.status != 400 or self.header is None:
                raise
            # Discord не нашёл шапку среди вложений сообщения — прикладываем её заново.
            await edit(view=self.build_panel(guild), attachments=self.header_files())

    async def refresh_panel(self, guild: discord.Guild) -> None:
        state = self.state(guild.id)
        if state.panel is None:
            return
        panel = state.panel
        message = self.get_partial_messageable(panel[0], guild_id=guild.id).get_partial_message(panel[1])
        try:
            await self._edit_panel(message.edit, guild)
        except (discord.NotFound, discord.Forbidden) as exc:
            if state.panel == panel:  # пульт могли уже перенести, пока шёл запрос
                log.warning(
                    "Пульт на сервере «%s» удалён или недоступен (%s). Чтобы вернуть его, напишите /radio-setup.",
                    guild.name,
                    exc.text or exc.status,
                )
                self._set_panel(guild, None)
        except discord.HTTPException as exc:
            log.warning("Не удалось обновить пульт: %s", exc)

    async def on_message(self, message: discord.Message) -> None:
        # Если в канале с пультом пишут, пульт уезжает вверх. Когда переписка затихнет, вернём его вниз.
        if message.guild is None or (self.user is not None and message.author.id == self.user.id):
            return
        state = self.states.get(message.guild.id)
        if state is None or state.panel is None or message.channel.id != state.panel[0]:
            return
        if state.bump_task is not None:
            state.bump_task.cancel()
        state.bump_task = asyncio.create_task(self._bump_panel(message.guild))

    async def _bump_panel(self, guild: discord.Guild) -> None:
        try:
            await asyncio.sleep(BUMP_SECONDS)
        except asyncio.CancelledError:
            return
        self.state(guild.id).bump_task = None
        try:
            await self._repost_panel(guild)
        except discord.HTTPException as exc:
            log.warning("Не удалось перенести пульт вниз: %s", exc)

    # ---------- обработчики команд и кнопок ----------

    @staticmethod
    async def _defer(interaction: discord.Interaction, from_panel: bool) -> None:
        if from_panel:
            await interaction.response.defer()  # кнопка: сообщение с пультом обновим следом
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)

    async def _respond(self, interaction: discord.Interaction, *, from_panel: bool, text: str) -> None:
        """Отвечает на нажатие или команду и обновляет постоянный пульт."""
        guild = interaction.guild
        assert guild is not None
        clicked = None
        if from_panel and interaction.message is not None:
            await self._edit_panel(interaction.edit_original_response, guild)
            clicked = interaction.message.id
        else:
            await interaction.followup.send(text, ephemeral=True)
        state = self.state(guild.id)
        if state.panel is not None and state.panel[1] != clicked:
            await self.refresh_panel(guild)

    async def handle_play(self, interaction: discord.Interaction, station: Station, *, from_panel: bool) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("Это работает только на сервере.", ephemeral=True)
            return
        # Бот идёт в голосовой канал того, кто нажал кнопку.
        channel = member.voice.channel if member.voice else None
        if channel is None:
            await interaction.response.send_message(
                f"{self.icons.get('radio_listen', '🎧')} Сначала зайдите в голосовой канал, "
                "а потом нажмите кнопку — бот придёт к вам.",
                ephemeral=True,
            )
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.connect and perms.speak):
            await interaction.response.send_message(
                f"🚫 У бота нет прав подключаться или говорить в канале {channel.mention}. "
                "Попросите администратора выдать боту права «Подключаться» и «Говорить».",
                ephemeral=True,
            )
            return

        await self._defer(interaction, from_panel)
        try:
            await self.play(guild, channel, station)
        except Exception:
            log.exception("Не удалось включить поток")
            await interaction.followup.send(
                "😕 Не получилось подключиться к голосовому каналу. Попробуйте ещё раз через пару секунд.",
                ephemeral=True,
            )
            return
        log.info("%s включил(а) %s", member, station.name)
        await self._respond(
            interaction,
            from_panel=from_panel,
            text=f"{self.icons.get('radio_eq', '▶️')} Включил **{self.station_emoji(station)} {station.name}** "
            f"в {channel.mention}",
        )

    async def handle_stop(self, interaction: discord.Interaction, *, from_panel: bool) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Это работает только на сервере.", ephemeral=True)
            return
        await self._defer(interaction, from_panel)
        was_playing = self.voice(guild) is not None
        await self.stop(guild)
        if was_playing:
            log.info("%s выключил(а) радио", interaction.user)
        await self._respond(
            interaction,
            from_panel=from_panel,
            text=f"{self.icons.get('radio_stop', '⏹')} Радио выключено." if was_playing else "Радио и так выключено.",
        )

    async def handle_volume(self, interaction: discord.Interaction, volume: float, *, from_panel: bool) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Это работает только на сервере.", ephemeral=True)
            return
        await self._defer(interaction, from_panel)
        volume = self.set_volume(guild, volume)
        louder = self.icons.get("radio_louder", "🔊")
        await self._respond(interaction, from_panel=from_panel, text=f"{louder} Громкость: {round(volume * 100)}%")

    async def handle_setup(self, interaction: discord.Interaction) -> None:
        guild, channel = interaction.guild, interaction.channel
        if guild is None or not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message("Это работает только в текстовом канале сервера.", ephemeral=True)
            return
        missing = [title for perm, title in PANEL_PERMISSIONS.items() if not getattr(interaction.app_permissions, perm)]
        if missing:
            await interaction.response.send_message(
                "🚫 В этом канале боту не хватает прав: **" + ", ".join(missing) + "**.\n"
                "Откройте настройки канала → «Права доступа», добавьте туда бота и включите эти права.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self._post_panel(guild, channel)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"😕 Не получилось разместить пульт: {exc.text or exc}", ephemeral=True)
            return
        log.info("Пульт размещён на сервере «%s» в канале #%s", guild.name, getattr(channel, "name", channel.id))
        await interaction.followup.send(
            f"✅ Готово! Пульт размещён в <#{channel.id}>. Пользоваться им может любой, кто видит этот канал.",
            ephemeral=True,
        )

    # ---------- «что сейчас играет» ----------

    async def _poll_now_playing(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                assert self._session is not None
                if await self.now.refresh(self._session):
                    await self._update_presence()
                    for guild_id, state in list(self.states.items()):
                        guild = self.get_guild(guild_id)
                        if state.panel is not None and guild is not None:
                            await self.refresh_panel(guild)
            except Exception:
                log.exception("Ошибка при обновлении названий треков")
            await asyncio.sleep(POLL_SECONDS)

    async def _update_presence(self) -> None:
        playing = next((s.station for s in self.states.values() if s.station is not None), None)
        if playing is not None:
            track = self.now.get(playing)
            text = f"{playing.name}: {track}" if track else playing.title
        else:
            text = self.playlist.name
        text = text[:128]
        if text == self._presence or not self.is_ready():
            return
        self._presence = text
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=text))


class StationButton(discord.ui.Button["RadioPanel"]):
    def __init__(self, bot: RadioBot, index: int, station: Station, active: bool) -> None:
        super().__init__(
            label=station.name,
            emoji=bot.station_emoji(station),
            # Играющий поток подсвечивается фиолетовым — в тон неоновой рамке.
            style=discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary,
            custom_id=f"radio:station:{index}",
        )
        self.bot = bot
        self.station = station

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.handle_play(interaction, self.station, from_panel=True)


class ControlButton(discord.ui.Button["RadioPanel"]):
    def __init__(
        self, bot: RadioBot, action: str, label: str, emoji: discord.Emoji | str, style: discord.ButtonStyle
    ) -> None:
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"radio:{action}")
        self.bot = bot
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action == "stop":
            await self.bot.handle_stop(interaction, from_panel=True)
            return
        # Шаг по 10%: 50 → 60 → 70…; самое тихое — 5%.
        percent = round(self.bot.state(interaction.guild_id or 0).volume * 100)
        if self.action == "louder":
            percent = min(100, (percent // VOLUME_STEP_PERCENT + 1) * VOLUME_STEP_PERCENT)
        else:
            percent = max(5, (percent - 1) // VOLUME_STEP_PERCENT * VOLUME_STEP_PERCENT)
        await self.bot.handle_volume(interaction, percent / 100, from_panel=True)


class RadioPanel(discord.ui.LayoutView):
    """Пульт на Components V2: неоновая рамка, шапка, что играет и кнопки. Собирается в RadioBot.build_panel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


def register_commands(bot: RadioBot) -> None:
    choices = [app_commands.Choice(name=st.name[:100], value=i) for i, st in enumerate(bot.stations)]

    @bot.tree.command(name="radio-setup", description="Поставить постоянный пульт радио в этот канал")
    @app_commands.default_permissions(manage_guild=True)
    async def setup(interaction: discord.Interaction) -> None:
        await bot.handle_setup(interaction)

    @bot.tree.command(name="radio", description="Открыть личный пульт радио (видно только вам)")
    async def radio(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.send_message(
            view=bot.build_panel(interaction.guild), files=bot.header_files(), ephemeral=True
        )

    @bot.tree.command(name="radio-play", description="Включить поток в вашем голосовом канале")
    @app_commands.rename(stream="поток")
    @app_commands.describe(stream="Какой поток включить")
    @app_commands.choices(stream=choices)
    async def play(interaction: discord.Interaction, stream: app_commands.Choice[int]) -> None:
        await bot.handle_play(interaction, bot.stations[stream.value], from_panel=False)

    @bot.tree.command(name="radio-stop", description="Выключить радио и выйти из голосового канала")
    async def stop(interaction: discord.Interaction) -> None:
        await bot.handle_stop(interaction, from_panel=False)

    @bot.tree.command(name="radio-now", description="Что сейчас играет на всех потоках")
    async def now(interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await interaction.response.send_message(
            view=bot.build_panel(interaction.guild, buttons=False), files=bot.header_files(), ephemeral=True
        )

    @bot.tree.command(name="radio-volume", description="Изменить громкость радио (от 1 до 100)")
    @app_commands.rename(level="уровень")
    @app_commands.describe(level="Громкость в процентах, от 1 до 100")
    async def volume(interaction: discord.Interaction, level: app_commands.Range[int, 1, 100]) -> None:
        await bot.handle_volume(interaction, level / 100, from_panel=False)

    @bot.tree.error
    async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        log.error("Ошибка в команде", exc_info=error)
        text = "😕 Что-то пошло не так. Подробности — в логах бота."
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)


def _load_opus() -> bool:
    """Opus кодирует звук для Discord. В Windows библиотека идёт вместе с discord.py, в Linux — системная."""
    if discord.opus.is_loaded():
        return True
    load_default = getattr(discord.opus, "_load_default", None)
    if load_default is not None and load_default():
        return True
    with contextlib.suppress(OSError):
        discord.opus.load_opus(ctypes.util.find_library("opus") or "libopus.so.0")
    return discord.opus.is_loaded()


def _check_voice_support(config: Config) -> None:
    """Пишет в журнал, всё ли на месте для звука, — чтобы проблему было видно сразу при запуске."""
    from discord import voice_client, voice_state

    parts = {
        "ffmpeg": bool(config.ffmpeg),
        "Opus": _load_opus(),
        "PyNaCl": voice_client.has_nacl,
        "DAVE": voice_state.has_dave,
    }
    summary = ", ".join(f"{name} {'✓' if ok else '✗'}" for name, ok in parts.items())
    if all(parts.values()):
        log.info("Звук: %s", summary)
    else:
        log.error("Звук: %s — бот не сможет играть в голосовом канале!", summary)


def _setup_file_log(config: Config) -> None:
    """Дублирует журнал в data/bot.log — чтобы можно было посмотреть, что было, даже после закрытия окна."""
    try:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            config.data_dir / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Не удалось открыть файл журнала: %s", exc)
        return
    handler.setFormatter(
        logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{")
    )
    logging.getLogger().addHandler(handler)


def run() -> None:
    discord.utils.setup_logging(level=logging.INFO)
    # Сообщения «ffmpeg process … has not terminated» при каждом переключении потока — не ошибка, скрываем.
    logging.getLogger("discord.player").setLevel(logging.WARNING)
    config = load_config()
    _setup_file_log(config)
    try:
        playlist = load_playlist(config.playlist)
    except (OSError, ValueError, ET.ParseError) as exc:
        sys.exit(f"Не удалось прочитать плейлист {config.playlist.name}: {exc}")
    log.info(
        "Плейлист %s — «%s»: %s",
        config.playlist.name,
        playlist.name,
        ", ".join(f"{s.name} ({s.url})" for s in playlist.stations),
    )
    _check_voice_support(config)

    bot = RadioBot(config, playlist)
    try:
        bot.run(config.token, log_handler=None)
    except discord.LoginFailure:
        sys.exit("Discord не принял токен. Проверьте токен в файле .env (README.md, шаг 1).")


def main() -> None:
    # Журнал всегда в UTF-8, даже если вывод перенаправлен в файл (иначе ломаются эмодзи и «ёлочки»).
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not FROZEN:
        run()
        return
    # Запуск двойным щелчком по .exe: окно не должно закрываться молча, если что-то не так.
    with contextlib.suppress(Exception):
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleTitleW("Радио-бот — пока окно открыто, бот работает")
        # Выделение мышью в старом окне консоли (QuickEdit) замораживает вывод, а с ним и бота — отключаем.
        stdin = kernel32.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(stdin, ctypes.byref(mode)):
            kernel32.SetConsoleMode(stdin, (mode.value | 0x0080) & ~0x0040)
        # Приоритет чуть выше обычного: звук не запинается, когда компьютер занят (например, игрой).
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00008000)
    print("Радио-бот запускается. Чтобы выключить его, закройте это окно.\n", flush=True)
    try:
        run()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            print(f"\n[!] {exc.code}" if isinstance(exc.code, str) else f"\n[!] Код ошибки {exc.code}")
            input("\nНажмите Enter, чтобы закрыть окно…")
    except Exception:
        log.exception("Бот аварийно завершился")
        input("\nНажмите Enter, чтобы закрыть окно…")
