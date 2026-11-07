from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from dataclasses import replace
from html import escape
from pathlib import Path
from uuid import uuid4

from aiogram import Dispatcher, Router
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle, InlineQueryResultCachedAudio, InlineQueryResultsButton, InputTextMessageContent, Message)

from cookie_export import MAX_UPLOAD_BYTES, read_netscape_cookie, write_cookie
from .catalog import AppleCatalog, Track, parse_apple_url
from .constants import MAX_FILE_BYTES, MAX_RESULTS
from .downloader import run_download, wrapper_ready
from .errors import CatalogError, DownloadError
from .menu import about_page, connect_page, help_page, home_page, session_page
from .settings import Settings
from .telegram import Telegram

LOG = logging.getLogger("music_bot")

class Bot:
    def __init__(self, settings: Settings, telegram: Telegram, catalog: AppleCatalog):
        self.settings = settings
        self.telegram = telegram
        self.catalog = catalog
        self.choices: dict[str, tuple[float, int, object]] = {}
        self.jobs: set[asyncio.Task] = set()
        self.download_lock = asyncio.Lock()
        self.pending_connect: dict[int, float] = {}
        self.bot_username: str | None = None
        self.audio_cache: dict[str, str] = {}

    def session_path(self, user_id: int) -> Path | None:
        uploaded = self.settings.session_dir / f"{user_id}.cookies.txt"
        if uploaded.is_file():
            return uploaded
        if user_id == self.settings.user_id:
            return self.settings.cookies_path
        return None

    def catalog_for(self, user_id: int) -> AppleCatalog | None:
        path = self.session_path(user_id)
        if path is None:
            return None
        return self.catalog if path == self.settings.cookies_path else AppleCatalog(path)

    async def send_menu(self, chat_id: int, page: str = "home") -> None:
        content, buttons = self.menu_page(chat_id, page)
        await self.telegram.message(chat_id, content, buttons, parse_mode="HTML")

    def menu_page(self, user_id: int, page: str) -> tuple[str, list[list[dict]]]:
        if page == "about":
            return about_page(self.settings)
        if page == "help":
            return help_page(self.bot_username)
        if page == "session":
            uploaded = (self.settings.session_dir / f"{user_id}.cookies.txt").is_file()
            return session_page(
                self.session_path(user_id) is not None, uploaded,
                user_id == self.settings.user_id and self.settings.cookies_path.is_file(),
            )
        return home_page(self.bot_username, self.session_path(user_id) is not None)

    async def handle_menu(self, callback: CallbackQuery, page: str) -> None:
        message = callback.message
        if message is None:
            return
        chat_id = message.chat.id
        user_id = callback.from_user.id
        if page in {"home", "about", "help", "session"}:
            content, buttons = self.menu_page(user_id, page)
        elif page == "connect":
            self.pending_connect[user_id] = time.monotonic()
            content, buttons = connect_page(self.settings)
        elif page == "disconnect_confirm":
            content = "<b>Удалить сохранённую сессию?</b>\nПосле удаления потребуется снова отправить cookies.txt."
            buttons = [
                [{"text": "Удалить", "callback_data": "menu:disconnect", "style": "danger"}],
                [{"text": "Отмена", "callback_data": "menu:session"}],
            ]
        elif page == "disconnect":
            (self.settings.session_dir / f"{user_id}.cookies.txt").unlink(missing_ok=True)
            self.pending_connect.pop(user_id, None)
            content, buttons = self.menu_page(user_id, "session")
        elif page == "playlists":
            catalog = self.catalog_for(user_id)
            if catalog is None:
                content = "<b>📚 Мои плейлисты</b>\n\nСначала подключите сессию Apple Music."
                buttons = [[{"text": "🔑 Подключить", "callback_data": "menu:connect", "style": "success"}],
                           [{"text": "← В меню", "callback_data": "menu:home"}]]
            else:
                try:
                    items = await catalog.library_playlists()
                    await self.show_library_playlists(chat_id, items)
                    return
                except CatalogError as exc:
                    content = f"<b>📚 Мои плейлисты</b>\n\n{escape(str(exc))}"
                except Exception as exc:
                    LOG.error("Listing playlists failed: %s", type(exc).__name__)
                    content = "<b>📚 Мои плейлисты</b>\n\nНе удалось прочитать плейлисты. Проверьте сессию."
                buttons = [[{"text": "← В меню", "callback_data": "menu:home"}]]
        else:
            return
        await self.telegram.edit_message(chat_id, message.message_id, content, buttons, parse_mode="HTML")

    def choice(self, value: object, owner_id: int) -> str:
        now = time.monotonic()
        self.choices = {k: v for k, v in self.choices.items() if now - v[0] < 1800}
        key = uuid4().hex[:12]
        self.choices[key] = (now, owner_id, value)
        return key

    @staticmethod
    def audio_cache_key(user_id: int, track: Track) -> str:
        return track.url or f"{user_id}:{track.library_id}"

    async def show_tracks(self, chat_id: int, title: str, tracks: list[Track], start: int = 0) -> None:
        if not tracks:
            await self.telegram.message(chat_id, "Треки не найдены или недоступны в вашем регионе.")
            return
        buttons = []
        for track in tracks[start:start + MAX_RESULTS]:
            key = self.choice(track, chat_id)
            label = f"{track.artist} — {track.title}"[:55]
            row = [{"text": label, "callback_data": "aac:" + key}]
            if chat_id == self.settings.user_id and self.settings.enable_alac:
                row.append({"text": "ALAC", "callback_data": "alac:" + key})
            buttons.append(row)
        if start + MAX_RESULTS < len(tracks):
            next_key = self.choice((title, tracks, start + MAX_RESULTS), chat_id)
            buttons.append([{"text": "Ещё треки →", "callback_data": "more:" + next_key}])
        await self.telegram.message(chat_id, f"{title} ({start + 1}–{min(start + MAX_RESULTS, len(tracks))} из {len(tracks)})", buttons)

    async def show_library_playlists(self, chat_id: int, items: list[dict], start: int = 0) -> None:
        if not items:
            await self.telegram.message(chat_id, "Плейлисты не найдены.")
            return
        buttons = []
        for item in items[start:start + MAX_RESULTS]:
            key = self.choice(item["id"], chat_id)
            name = (item.get("attributes") or {}).get("name", "Плейлист")
            buttons.append([{"text": name[:55], "callback_data": "playlist:" + key}])
        if start + MAX_RESULTS < len(items):
            key = self.choice((items, start + MAX_RESULTS), chat_id)
            buttons.append([{"text": "Ещё плейлисты →", "callback_data": "pagemenu:" + key}])
        await self.telegram.message(chat_id, f"Ваши плейлисты ({start + 1}–{min(start + MAX_RESULTS, len(items))} из {len(items)}):", buttons)

    async def connect_document(self, message: Message) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id
        if time.monotonic() - self.pending_connect.get(user_id, 0) >= 600:
            await self.telegram.message(chat_id, "Сначала отправьте /connect, затем файл cookies.txt в течение 10 минут.")
            return
        document = message.document
        temporary: Path | None = None
        try:
            if not (document.file_name or "").lower().endswith(".txt"):
                raise ValueError("Нужен текстовый файл cookies.txt.")
            if document.file_size is None or document.file_size > MAX_UPLOAD_BYTES:
                raise ValueError("Файл cookies.txt слишком большой.")
            raw = await self.telegram.download_document(document)
            cookie = read_netscape_cookie(raw)
            directory = self.settings.session_dir
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = directory / f"{user_id}.pending.cookies.txt"
            write_cookie(temporary, cookie, replace=True)

            async def check_subscription(api):
                return True

            await AppleCatalog(temporary)._with_api(check_subscription)
            os.replace(temporary, directory / f"{user_id}.cookies.txt")
            self.pending_connect.pop(user_id, None)
            await self.telegram.message(chat_id, "Сессия Apple Music подключена к вашему Telegram ID. /disconnect удалит её с сервера.")
        except (ValueError, CatalogError) as exc:
            await self.telegram.message(chat_id, f"Не удалось подключить сессию: {exc}")
        except Exception as exc:
            LOG.error("Session connection failed: %s", type(exc).__name__)
            await self.telegram.message(chat_id, "Не удалось сохранить сессию. Повторите /connect.")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            try:
                await self.telegram.delete_message(chat_id, message.message_id)
            except Exception:
                LOG.warning("Could not delete uploaded cookie message")

    def queue_download(self, chat_id: int, track: Track, quality: str = "aac") -> None:
        job = asyncio.create_task(self.download_and_send(chat_id, track, quality))
        self.jobs.add(job)
        job.add_done_callback(self.jobs.discard)

    async def handle_inline(self, query: InlineQuery) -> None:
        user_id = query.from_user.id
        if not self.settings.is_allowed(user_id):
            await self.telegram.answer_inline(query.id, [])
            return
        catalog = self.catalog_for(user_id)
        if catalog is None:
            await self.telegram.answer_inline(
                query.id, [], InlineQueryResultsButton(
                    text="Подключить Apple Music в боте", start_parameter="connect",
                ),
            )
            return
        text = query.query.strip()[:200]
        if not text:
            await self.telegram.answer_inline(
                query.id, [], InlineQueryResultsButton(
                    text="Введите название или ссылку на трек", start_parameter="home",
                ),
            )
            return
        try:
            parsed = parse_apple_url(text) if text.startswith("https://") else None
            if parsed:
                kind, item_id = parsed
                if kind == "song":
                    found = await catalog.song(item_id)
                    tracks = [found] if found else []
                elif kind == "library_song":
                    found = await catalog.library_song(item_id)
                    tracks = [found] if found else []
                else:
                    _, tracks = await catalog.playlist(item_id)
                    tracks = tracks[:MAX_RESULTS]
            elif text.startswith(("http://", "https://")):
                tracks = []
            else:
                tracks = await catalog.search(text)
            if self.bot_username is None:
                self.bot_username = await self.telegram.username()
            results = []
            for track in tracks[:MAX_RESULTS]:
                key = self.choice(track, user_id)
                cached_id = self.audio_cache.get(self.audio_cache_key(user_id, track))
                if cached_id:
                    results.append(InlineQueryResultCachedAudio(
                        id=key, audio_file_id=cached_id,
                        caption=f"{track.artist} — {track.title} · AAC",
                    ))
                    continue
                link = f"https://t.me/{self.bot_username}?start=dl_{key}"
                buttons = [InlineKeyboardButton(text="Загрузить AAC в этот чат", callback_data="inline_aac:" + key)]
                if user_id == self.settings.user_id and self.settings.enable_alac:
                    buttons.append(InlineKeyboardButton(
                        text="Скачать ALAC в личке",
                        url=f"https://t.me/{self.bot_username}?start=al_{key}",
                    ))
                result = InlineQueryResultArticle(
                    id=key,
                    title=track.title,
                    description=f"{track.artist} · AAC" + (" / ALAC" if len(buttons) > 1 else ""),
                    input_message_content=InputTextMessageContent(
                        message_text=f"{track.artist} — {track.title}\n{track.url}".strip(),
                    ),
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]),
                )
                results.append(result)
            await self.telegram.answer_inline(query.id, results)
        except Exception as exc:
            LOG.error("Inline search failed: %s", type(exc).__name__)
            await self.telegram.answer_inline(
                query.id, [], InlineQueryResultsButton(
                    text="Поиск не удался — открыть бота", start_parameter="home",
                ),
            )

    async def handle_message(self, message: Message) -> None:
        if not message.from_user or not self.settings.is_allowed(message.from_user.id) or message.chat.type != "private":
            return
        chat_id = message.chat.id
        user_id = message.from_user.id
        if message.document:
            await self.connect_document(message)
            return
        text = (message.text or "").strip()
        if text.startswith(("/start dl_", "/start al_")):
            quality = "alac" if text.startswith("/start al_") else "aac"
            key = text[len("/start dl_"):]
            entry = self.choices.get(key)
            if not entry or entry[1] != user_id or time.monotonic() - entry[0] >= 1800 or not isinstance(entry[2], Track):
                await self.telegram.message(chat_id, "Ссылка устарела. Повторите поиск через @бота.")
            elif self.session_path(user_id) is None:
                await self.telegram.message(chat_id, "Сначала подключите свою сессию командой /connect.")
            else:
                self.queue_download(chat_id, entry[2], quality)
            return
        if text in {"/start", "/start home"}:
            await self.send_menu(chat_id)
            return
        if text == "/help":
            await self.send_menu(chat_id, "help")
            return
        if text == "/about":
            await self.send_menu(chat_id, "about")
            return
        if text in {"/connect", "/start connect"}:
            self.pending_connect[user_id] = time.monotonic()
            content, buttons = connect_page(self.settings)
            await self.telegram.message(chat_id, content, buttons, parse_mode="HTML")
            return
        if text == "/disconnect":
            uploaded = self.settings.session_dir / f"{user_id}.cookies.txt"
            uploaded.unlink(missing_ok=True)
            self.pending_connect.pop(user_id, None)
            suffix = " Для вашего аккаунта также задан APPLE_MUSIC_COOKIES_PATH в .env; удалите его вручную, если хотите отключить и эту сессию." if user_id == self.settings.user_id else ""
            await self.telegram.message(chat_id, "Загруженная сессия удалена с сервера." + suffix)
            return
        catalog = self.catalog_for(user_id)
        if catalog is None:
            await self.telegram.message(chat_id, "Сначала подключите свою сессию командой /connect.")
            return
        if text == "/playlists":
            try:
                playlists = await catalog.library_playlists()
                await self.show_library_playlists(chat_id, playlists)
            except CatalogError as exc:
                await self.telegram.message(chat_id, str(exc))
            except Exception as exc:
                LOG.error("Listing playlists failed: %s", type(exc).__name__)
                await self.telegram.message(chat_id, "Не удалось прочитать плейлисты. Проверьте cookies и подписку.")
            return
        if not text:
            return
        if text.startswith("http://") or text.startswith("https://"):
            parsed = parse_apple_url(text)
            if not parsed:
                await self.telegram.message(chat_id, "Поддерживаются ссылки на трек или плейлист music.apple.com.")
                return
            kind, item_id = parsed
            try:
                if kind in {"song", "library_song"}:
                    track = await (catalog.song(item_id) if kind == "song" else catalog.library_song(item_id))
                    if not track:
                        raise RuntimeError("Трек не найден.")
                    title = "Выберите качество:" if user_id == self.settings.user_id and self.settings.enable_alac else "Скачать трек в AAC:"
                    await self.show_tracks(chat_id, title, [track])
                else:
                    title, tracks = await catalog.playlist(item_id)
                    await self.show_tracks(chat_id, title, tracks)
            except CatalogError as exc:
                await self.telegram.message(chat_id, str(exc))
            except Exception as exc:
                LOG.error("Resolving Apple Music link failed: %s", type(exc).__name__)
                await self.telegram.message(chat_id, "Не удалось открыть ссылку. Проверьте сессию и доступность в вашем регионе.")
            return
        try:
            tracks = await catalog.search(text[:200])
            await self.show_tracks(chat_id, "Результаты поиска · AAC по умолчанию:", tracks)
        except CatalogError as exc:
            await self.telegram.message(chat_id, str(exc))
        except Exception as exc:
            LOG.error("Search failed: %s", type(exc).__name__)
            await self.telegram.message(chat_id, "Поиск не удался. Проверьте cookies и подписку Apple Music.")

    async def handle_callback(self, callback: CallbackQuery) -> None:
        if not self.settings.is_allowed(callback.from_user.id):
            return
        if (callback.data or "").startswith("inline_aac:") and callback.inline_message_id:
            await self.telegram.answer_callback(callback.id)
            key = callback.data.partition(":")[2]
            entry = self.choices.get(key)
            if not entry or entry[1] != callback.from_user.id or time.monotonic() - entry[0] >= 1800 or not isinstance(entry[2], Track):
                await self.telegram.message(callback.from_user.id, "Выбор устарел. Повторите inline-поиск.")
                return
            job = asyncio.create_task(self.download_inline_audio(callback.from_user.id, callback.inline_message_id, entry[2]))
            self.jobs.add(job)
            job.add_done_callback(self.jobs.discard)
            return
        message = callback.message
        if message is None or message.chat.type != "private":
            return
        chat_id = message.chat.id
        await self.telegram.answer_callback(callback.id)
        action, _, key = (callback.data or "").partition(":")
        if action == "menu":
            await self.handle_menu(callback, key)
            return
        entry = self.choices.get(key)
        if not entry or entry[1] != callback.from_user.id or time.monotonic() - entry[0] >= 1800:
            await self.telegram.message(chat_id, "Выбор устарел. Повторите поиск.")
            return
        value = entry[2]
        catalog = self.catalog_for(callback.from_user.id)
        if catalog is None:
            await self.telegram.message(chat_id, "Сначала подключите свою сессию командой /connect.")
            return
        if action == "playlist" and isinstance(value, str):
            try:
                title, tracks = await catalog.playlist(value)
                await self.show_tracks(chat_id, title, tracks)
            except CatalogError as exc:
                await self.telegram.message(chat_id, str(exc))
            except Exception as exc:
                LOG.error("Opening playlist failed: %s", type(exc).__name__)
                await self.telegram.message(chat_id, "Не удалось открыть плейлист.")
        elif action == "more" and isinstance(value, tuple):
            title, tracks, start = value
            await self.show_tracks(chat_id, title, tracks, start)
        elif action == "pagemenu" and isinstance(value, tuple):
            items, start = value
            await self.show_library_playlists(chat_id, items, start)
        elif action in {"aac", "alac"} and isinstance(value, Track):
            self.queue_download(chat_id, value, action)

    async def download_and_send(self, chat_id: int, track: Track, quality: str) -> None:
        catalog = self.catalog_for(chat_id)
        cookie_path = self.session_path(chat_id)
        if catalog is None or cookie_path is None:
            await self.telegram.message(chat_id, "Сначала подключите свою сессию командой /connect.")
            return
        if quality == "alac":
            if chat_id != self.settings.user_id:
                await self.telegram.message(chat_id, "ALAC для дополнительных пользователей требует отдельный wrapper-v2 с их аккаунтом; сейчас доступен AAC.")
                return
            if not self.settings.enable_alac:
                await self.telegram.message(chat_id, "ALAC пока не включён. Настройте wrapper-v2 и ENABLE_ALAC=true; доступен AAC.")
                return
            if not await wrapper_ready(self.settings):
                await self.telegram.message(chat_id, "Wrapper для ALAC не готов. Проверьте его /health; доступен AAC.")
                return
        await self.telegram.message(chat_id, f"Загружаю {track.artist} — {track.title} ({quality.upper()})…")
        async with self.download_lock:
            try:
                resolved = await catalog.resolve_library_track(track)
                if not resolved:
                    await self.telegram.message(chat_id, "Для этого трека из медиатеки нет доступной записи в каталоге Apple Music.")
                    return
                with tempfile.TemporaryDirectory(prefix="music-bot-") as folder:
                    root = Path(folder)
                    output, temp = root / "output", root / "temp"
                    output.mkdir()
                    temp.mkdir()
                    user_settings = replace(self.settings, cookies_path=cookie_path)
                    file = await run_download(user_settings, resolved.url, quality, output, temp)
                    if file.stat().st_size > MAX_FILE_BYTES:
                        suffix = " Выберите AAC для этого трека." if quality == "alac" else ""
                        await self.telegram.message(chat_id, "Файл больше лимита Telegram Bot API (50 МБ)." + suffix)
                        return
                    sent = await self.telegram.send_file(chat_id, file, resolved, quality)
                    if quality == "aac" and sent and sent.audio:
                        self.audio_cache[self.audio_cache_key(chat_id, track)] = sent.audio.file_id
            except CatalogError as exc:
                await self.telegram.message(chat_id, str(exc))
            except DownloadError as exc:
                await self.telegram.message(chat_id, str(exc))
            except Exception as exc:
                LOG.error("Download/send failed: %s", type(exc).__name__)
                await self.telegram.message(chat_id, "Загрузка или отправка не удалась. Проверьте сессию, wrapper и доступность трека.")

    async def download_inline_audio(self, user_id: int, inline_message_id: str, track: Track) -> None:
        catalog = self.catalog_for(user_id)
        cookie_path = self.session_path(user_id)
        if catalog is None or cookie_path is None:
            await self.telegram.message(user_id, "Сначала подключите свою сессию командой /connect.")
            return
        try:
            async with self.download_lock:
                cached_id = self.audio_cache.get(self.audio_cache_key(user_id, track))
                if cached_id:
                    await self.telegram.edit_inline_audio(inline_message_id, cached_id, track)
                    return
                await self.telegram.edit_inline_status(
                    inline_message_id, f"Загружаю {track.artist} — {track.title} (AAC)…",
                )
                resolved = await catalog.resolve_library_track(track)
                if not resolved:
                    raise DownloadError("Трек недоступен в каталоге Apple Music.")
                with tempfile.TemporaryDirectory(prefix="music-bot-") as folder:
                    root = Path(folder)
                    output, temp = root / "output", root / "temp"
                    output.mkdir()
                    temp.mkdir()
                    file = await run_download(
                        replace(self.settings, cookies_path=cookie_path), resolved.url,
                        "aac", output, temp,
                    )
                    if file.stat().st_size > MAX_FILE_BYTES:
                        raise DownloadError("Файл больше лимита Telegram Bot API (50 МБ).")
                    staged = await self.telegram.send_file(user_id, file, resolved, "aac")
                    try:
                        if not staged or not staged.audio:
                            raise DownloadError("Не удалось подготовить аудиофайл для inline-отправки.")
                        file_id = staged.audio.file_id
                        self.audio_cache[self.audio_cache_key(user_id, track)] = file_id
                        await self.telegram.edit_inline_audio(inline_message_id, file_id, resolved)
                    finally:
                        if staged:
                            try:
                                await self.telegram.delete_message(user_id, staged.message_id)
                            except Exception:
                                LOG.warning("Could not delete staged inline audio message")
        except (CatalogError, DownloadError) as exc:
            try:
                await self.telegram.edit_inline_status(inline_message_id, f"Не удалось загрузить: {exc}")
            except Exception:
                pass
            await self.telegram.message(user_id, str(exc))
        except Exception as exc:
            LOG.error("Inline download failed: %s", type(exc).__name__)
            try:
                await self.telegram.edit_inline_status(inline_message_id, "Загрузка не удалась. Повторите inline-поиск.")
            except Exception:
                pass
            await self.telegram.message(user_id, "Не удалось загрузить трек в чат. Проверьте сессию и повторите поиск.")

    def dispatcher(self) -> Dispatcher:
        router = Router()
        router.message.register(self.handle_message)
        router.callback_query.register(self.handle_callback)
        router.inline_query.register(self.handle_inline)
        dispatcher = Dispatcher()
        dispatcher.include_router(router)
        return dispatcher

    async def run(self) -> None:
        dispatcher = self.dispatcher()
        self.bot_username = await self.telegram.username()
        await dispatcher.start_polling(
            self.telegram.bot,
            allowed_updates=["message", "callback_query", "inline_query"],
        )
