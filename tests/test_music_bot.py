import asyncio
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from tempfile import TemporaryDirectory

from aiogram import Bot as AiogramBot
from aiogram.types import Chat, FSInputFile, InlineKeyboardMarkup, Message, Update, User

from music_bot import Bot, Settings, Telegram, Track, download_command, main, parse_apple_url
from cookie_export import netscape_cookie


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.files = []
        self.upload = b""
        self.deleted = []
        self.inline_answers = []
        self.inline_edits = []
        self.inline_statuses = []
        self.edited_messages = []

    async def message(self, chat_id, text, buttons=None, parse_mode=None):
        self.messages.append((chat_id, text, buttons))

    async def edit_message(self, chat_id, message_id, text, buttons, parse_mode=None):
        self.edited_messages.append((chat_id, message_id, text, buttons, parse_mode))

    async def send_file(self, chat_id, file, track, quality):
        self.files.append((chat_id, file.name, quality))
        return SimpleNamespace(
            message_id=len(self.files),
            audio=SimpleNamespace(file_id="cached-audio-id") if quality == "aac" else None,
        )

    async def answer_callback(self, callback_id):
        pass

    async def download_document(self, document):
        return self.upload

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def username(self):
        return "test_music_bot"

    async def answer_inline(self, query_id, results, button=None):
        self.inline_answers.append((query_id, results, button))

    async def edit_inline_audio(self, inline_message_id, file_id, track):
        self.inline_edits.append((inline_message_id, file_id, track))

    async def edit_inline_status(self, inline_message_id, text):
        self.inline_statuses.append((inline_message_id, text))


class FakeCatalog:
    def __init__(self):
        self.searches = []

    async def search(self, query):
        self.searches.append(query)
        return [Track("Test", "Artist", "https://music.apple.com/us/song/test/123")]

    async def resolve_library_track(self, track):
        return track

    async def song(self, item_id):
        return Track("Test", "Artist", f"https://music.apple.com/us/song/test/{item_id}")


class UrlTests(unittest.TestCase):
    def test_track_album_and_playlist_links(self):
        self.assertEqual(parse_apple_url("https://music.apple.com/us/song/test/123"), ("song", "123"))
        self.assertEqual(parse_apple_url("https://music.apple.com/us/album/test/456?i=123"), ("song", "123"))
        self.assertEqual(parse_apple_url("https://music.apple.com/us/playlist/test/pl.abc"), ("playlist", "pl.abc"))
        self.assertEqual(parse_apple_url("https://music.apple.com/library/songs/i.abc"), ("library_song", "i.abc"))
        self.assertIsNone(parse_apple_url("https://evil.example/us/song/test/123"))

    def test_quality_command_has_no_shell(self):
        settings = Settings("secret", 1, Path("cookies.txt"), enable_alac=True)
        command = download_command(settings, "https://music.apple.com/us/song/test/123", "alac", Path("out"), Path("tmp"))
        self.assertIn("alac", command)
        self.assertIn("--use-wrapper", command)
        self.assertNotIn("secret", command)

    def test_legacy_entrypoint_starts_async_bot(self):
        fake_bot = SimpleNamespace(run=AsyncMock())
        with patch("iridium_bot.main.Settings.from_env", return_value=Settings("secret", 42, Path("cookies.txt"))), \
             patch("iridium_bot.main.Telegram"), \
             patch("iridium_bot.main.Bot", return_value=fake_bot):
            main()
        fake_bot.run.assert_awaited_once()


class BotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.telegram = FakeTelegram()
        self.bot = Bot(Settings("secret", 42, Path("cookies.txt")), self.telegram, FakeCatalog())

    async def test_ignores_other_users_and_hides_disabled_alac(self):
        await self.bot.handle_message(SimpleNamespace(from_user=SimpleNamespace(id=43), chat=SimpleNamespace(id=43, type="private"), text="Test", document=None))
        self.assertEqual(self.telegram.messages, [])
        await self.bot.handle_message(SimpleNamespace(from_user=SimpleNamespace(id=42), chat=SimpleNamespace(id=42, type="private"), text="Test", document=None))
        buttons = self.telegram.messages[-1][2]
        self.assertTrue(buttons[0][0]["callback_data"].startswith("aac:"))
        self.assertEqual(len(buttons[0]), 1)

    async def test_start_uses_iridium_name(self):
        await self.bot.handle_message(SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42, type="private"),
            text="/start", document=None,
        ))
        self.assertIn("Iridium", self.telegram.messages[-1][1])
        buttons = self.telegram.messages[-1][2]
        self.assertEqual(buttons[0][0]["style"], "primary")
        self.assertEqual(buttons[1][0]["style"], "success")

    async def test_connect_shows_standalone_helper_and_guide(self):
        await self.bot.handle_message(SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=42, type="private"),
            text="/connect", document=None,
        ))
        buttons = self.telegram.messages[-1][2]
        self.assertEqual(buttons[0][0]["url"], "https://github.com/fayzetwin1/iridium/releases/latest")
        self.assertIn("COOKIE_EXPORT_FOR_FRIENDS.md", buttons[1][0]["url"])

    async def test_about_menu_uses_configured_links_and_edits_message(self):
        self.bot.settings = Settings(
            "secret", 42, Path("cookies.txt"),
            project_github_url="https://github.com/example/iridium",
            creator_name="<Author>", creator_url="https://t.me/example",
        )
        await self.bot.handle_callback(SimpleNamespace(
            id="cb", from_user=SimpleNamespace(id=42), data="menu:about",
            inline_message_id=None,
            message=SimpleNamespace(chat=SimpleNamespace(id=42, type="private"), message_id=7),
        ))
        chat_id, message_id, content, buttons, parse_mode = self.telegram.edited_messages[-1]
        self.assertEqual((chat_id, message_id, parse_mode), (42, 7, "HTML"))
        self.assertIn("Iridium", content)
        self.assertEqual(buttons[0][0]["url"], "https://github.com/example/iridium")
        self.assertEqual(buttons[1][0]["url"], "https://t.me/example")

    async def test_enabled_alac_is_offered_to_owner(self):
        self.bot.settings = Settings("secret", 42, Path("cookies.txt"), enable_alac=True)
        await self.bot.show_tracks(42, "Test", [Track("Test", "Artist", "https://music.apple.com/us/song/test/123")])
        buttons = self.telegram.messages[-1][2]
        self.assertTrue(buttons[0][1]["callback_data"].startswith("alac:"))
        self.bot.bot_username = "test_music_bot"
        await self.bot.handle_inline(SimpleNamespace(id="q", from_user=SimpleNamespace(id=42), query="Test"))
        result = self.telegram.inline_answers[-1][1][0]
        self.assertEqual(len(result.reply_markup.inline_keyboard[0]), 2)
        self.assertIn("?start=al_", result.reply_markup.inline_keyboard[0][1].url)

    async def test_inline_search_and_direct_link_open_private_download(self):
        self.bot.bot_username = "test_music_bot"
        user = SimpleNamespace(id=42)
        await self.bot.handle_inline(SimpleNamespace(id="q1", from_user=user, query="Test"))
        result = self.telegram.inline_answers[-1][1][0]
        self.assertEqual(result.title, "Test")
        callback_data = result.reply_markup.inline_keyboard[0][0].callback_data
        self.assertEqual(len(result.reply_markup.inline_keyboard[0]), 1)
        self.assertTrue(callback_data.startswith("inline_aac:"))
        self.assertIn("https://music.apple.com", result.input_message_content.message_text)
        with patch.object(self.bot, "download_and_send", new=AsyncMock()) as download:
            key = callback_data.split(":", 1)[1]
            await self.bot.handle_message(SimpleNamespace(
                from_user=user, chat=SimpleNamespace(id=42, type="private"),
                text="/start dl_" + key, document=None,
            ))
            await asyncio.sleep(0)
            download.assert_awaited_once()
        await self.bot.handle_inline(SimpleNamespace(
            id="q2", from_user=user,
            query="https://music.apple.com/us/song/test/123",
        ))
        self.assertEqual(self.telegram.inline_answers[-1][1][0].title, "Test")

    async def test_inline_callback_replaces_card_with_audio_and_caches_it(self):
        self.bot.bot_username = "test_music_bot"
        user = SimpleNamespace(id=42)
        await self.bot.handle_inline(SimpleNamespace(id="q1", from_user=user, query="global search"))
        self.assertEqual(self.bot.catalog.searches, ["global search"])
        result = self.telegram.inline_answers[-1][1][0]
        callback_data = result.reply_markup.inline_keyboard[0][0].callback_data

        async def fake_download(settings, url, quality, output, temp):
            file = output / "test.m4a"
            file.write_bytes(b"audio")
            return file

        with patch("iridium_bot.app.run_download", side_effect=fake_download):
            await self.bot.handle_callback(SimpleNamespace(
                id="callback", from_user=user, data=callback_data,
                inline_message_id="inline-message", message=None,
            ))
            await asyncio.gather(*self.bot.jobs)
        self.assertEqual(self.telegram.inline_edits[0][:2], ("inline-message", "cached-audio-id"))
        self.assertIn("Загружаю", self.telegram.inline_statuses[0][1])
        self.assertEqual(self.telegram.deleted[-1], (42, 1))
        await self.bot.handle_inline(SimpleNamespace(id="q2", from_user=user, query="global search"))
        self.assertEqual(self.telegram.inline_answers[-1][1][0].audio_file_id, "cached-audio-id")

    async def test_inline_requires_allowed_user_and_session(self):
        await self.bot.handle_inline(SimpleNamespace(id="q1", from_user=SimpleNamespace(id=43), query="Test"))
        self.assertEqual(self.telegram.inline_answers[-1][1], [])
        self.assertIsNone(self.telegram.inline_answers[-1][2])
        with TemporaryDirectory() as folder:
            settings = Settings("secret", 42, Path("missing.cookies.txt"), allowed_user_ids=frozenset({43}), session_dir=Path(folder))
            bot = Bot(settings, self.telegram, FakeCatalog())
            await bot.handle_inline(SimpleNamespace(id="q2", from_user=SimpleNamespace(id=43), query="Test"))
            self.assertEqual(self.telegram.inline_answers[-1][1], [])
            self.assertEqual(self.telegram.inline_answers[-1][2].start_parameter, "connect")

    async def test_aiogram_dispatcher_routes_message(self):
        dispatcher = self.bot.dispatcher()
        aiogram_bot = AiogramBot(token="123456:ABC")
        update = Update(
            update_id=1,
            message=Message(
                message_id=1, date=datetime.now(timezone.utc),
                chat=Chat(id=42, type="private"),
                from_user=User(id=42, is_bot=False, first_name="Owner"),
                text="Test",
            ),
        )
        try:
            await dispatcher.feed_update(aiogram_bot, update)
        finally:
            await aiogram_bot.session.close()
        self.assertEqual(len(self.telegram.messages), 1)

    async def test_large_alac_is_not_sent_and_aac_is_offered(self):
        self.bot.settings = Settings("secret", 42, Path("cookies.txt"), enable_alac=True)
        track = Track("Test", "Artist", "https://music.apple.com/us/song/test/123")

        async def fake_download(settings, url, quality, output, temp):
            file = output / "test.m4a"
            with file.open("wb") as handle:
                handle.truncate(50_000_001)
            return file

        with patch("iridium_bot.app.wrapper_ready", new=AsyncMock(return_value=True)), patch("iridium_bot.app.run_download", side_effect=fake_download):
            await self.bot.download_and_send(42, track, "alac")
        self.assertEqual(self.telegram.files, [])
        self.assertIn("AAC", self.telegram.messages[-1][1])

    async def test_friend_connects_and_disconnects_own_cookie(self):
        with TemporaryDirectory() as folder:
            settings = Settings(
                "secret", 42, Path("owner.cookies.txt"),
                allowed_user_ids=frozenset({43}), session_dir=Path(folder),
            )
            bot = Bot(settings, self.telegram, FakeCatalog())
            self.telegram.upload = netscape_cookie({
                "name": "media-user-token", "domain": ".music.apple.com",
                "value": "fake.test.token", "expires": 2000000000,
            }).encode()
            friend = SimpleNamespace(id=43)
            chat = SimpleNamespace(id=43, type="private")
            await bot.handle_message(SimpleNamespace(from_user=friend, chat=chat, text="/connect", document=None))
            document = SimpleNamespace(file_name="cookies.txt", file_size=len(self.telegram.upload))
            with patch("iridium_bot.catalog.AppleCatalog._with_api", new=AsyncMock(return_value=True)):
                await bot.handle_message(SimpleNamespace(from_user=friend, chat=chat, text=None, document=document, message_id=7))
            stored = Path(folder) / "43.cookies.txt"
            self.assertTrue(stored.is_file())
            self.assertEqual(self.telegram.deleted, [(43, 7)])
            self.assertEqual(bot.session_path(43), stored)
            track = Track("Test", "Artist", "https://music.apple.com/us/song/test/123")
            await bot.show_tracks(43, "Test", [track])
            self.assertEqual(len(self.telegram.messages[-1][2][0]), 1)
            used_paths = []

            async def fake_download(user_settings, url, quality, output, temp):
                used_paths.append(user_settings.cookies_path)
                file = output / "test.m4a"
                file.write_bytes(b"fake audio")
                return file

            with patch("iridium_bot.app.run_download", side_effect=fake_download):
                await bot.download_and_send(43, track, "aac")
            self.assertEqual(used_paths, [stored])
            await bot.handle_message(SimpleNamespace(from_user=friend, chat=chat, text="/disconnect", document=None))
            self.assertFalse(stored.exists())
            self.assertIsNone(bot.session_path(43))

    async def test_aiogram_transport_uses_audio_and_document(self):
        transport = Telegram.__new__(Telegram)
        transport.bot = SimpleNamespace(
            send_message=AsyncMock(), send_audio=AsyncMock(),
            send_document=AsyncMock(), answer_callback_query=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        track = Track("Test", "Artist", "https://music.apple.com/us/song/test/123")
        with self.subTest("AAC"):
            await transport.send_file(42, Path("aac.m4a"), track, "aac")
            self.assertIsInstance(transport.bot.send_audio.await_args.args[1], FSInputFile)
        with self.subTest("ALAC"):
            await transport.send_file(42, Path("alac.m4a"), track, "alac")
            self.assertIsInstance(transport.bot.send_document.await_args.args[1], FSInputFile)
        await transport.message(42, "Выберите", [[{"text": "AAC", "callback_data": "aac:key", "style": "primary"}]])
        markup = transport.bot.send_message.await_args.kwargs["reply_markup"]
        self.assertIsInstance(markup, InlineKeyboardMarkup)
        self.assertEqual(markup.inline_keyboard[0][0].style, "primary")
        await transport.edit_message(42, 7, "Меню", [[{"text": "Назад", "callback_data": "menu:home"}]], parse_mode="HTML")
        transport.bot.edit_message_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
