from __future__ import annotations

from io import BytesIO
from pathlib import Path

from aiogram import Bot as AiogramBot
from aiogram.types import (FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InlineQueryResultCachedAudio, InlineQueryResultsButton, InputMediaAudio, Message)

from .catalog import Track

class Telegram:
    def __init__(self, token: str):
        self.bot = AiogramBot(token=token)

    @staticmethod
    def keyboard(buttons: list[list[dict]] | None) -> InlineKeyboardMarkup | None:
        if not buttons:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(**button) for button in row] for row in buttons
        ])

    async def message(
        self, chat_id: int, text: str, buttons: list[list[dict]] | None = None,
        parse_mode: str | None = None,
    ) -> None:
        await self.bot.send_message(
            chat_id, text, reply_markup=self.keyboard(buttons), parse_mode=parse_mode,
        )

    async def edit_message(
        self, chat_id: int, message_id: int, text: str,
        buttons: list[list[dict]], parse_mode: str | None = None,
    ) -> None:
        await self.bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id,
            reply_markup=self.keyboard(buttons), parse_mode=parse_mode,
        )

    async def answer_callback(self, callback_id: str) -> None:
        await self.bot.answer_callback_query(callback_id)

    async def username(self) -> str:
        username = (await self.bot.get_me()).username
        if not username:
            raise RuntimeError("Bot username is missing")
        return username

    async def answer_inline(
        self, query_id: str, results: list[InlineQueryResultArticle | InlineQueryResultCachedAudio],
        button: InlineQueryResultsButton | None = None,
    ) -> None:
        await self.bot.answer_inline_query(
            query_id, results, cache_time=0, is_personal=True, button=button,
        )

    async def download_document(self, document) -> bytes:
        buffer = BytesIO()
        await self.bot.download(document, destination=buffer)
        return buffer.getvalue()

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self.bot.delete_message(chat_id, message_id)

    async def send_file(self, chat_id: int, file: Path, track: Track, quality: str) -> Message:
        upload = FSInputFile(file)
        caption = f"{track.artist} — {track.title} · {quality.upper()}"
        if quality == "alac":
            return await self.bot.send_document(chat_id, upload, caption=caption)
        else:
            return await self.bot.send_audio(
                chat_id, upload, caption=caption,
                title=track.title, performer=track.artist,
            )

    async def edit_inline_audio(self, inline_message_id: str, file_id: str, track: Track) -> None:
        await self.bot.edit_message_media(
            InputMediaAudio(media=file_id, title=track.title, performer=track.artist),
            inline_message_id=inline_message_id,
        )

    async def edit_inline_status(self, inline_message_id: str, text: str) -> None:
        await self.bot.edit_message_text(text, inline_message_id=inline_message_id)
