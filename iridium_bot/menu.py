"""Text and keyboard layouts for the private chat menu."""

from html import escape

from .settings import Settings

ButtonRows = list[list[dict[str, str]]]


def home_page(username: str | None, connected: bool) -> tuple[str, ButtonRows]:
    bot_name = f"@{username}" if username else "@имя_бота"
    status = "🟢 Сессия подключена" if connected else "🟡 Сессия не подключена"
    return (
        "<b>✦ Iridium</b>\n"
        "Музыка Apple Music в вашем Telegram. Найдите трек по названию или отправьте ссылку.\n\n"
        f"{status}\n"
        f"Для поиска в любом чате введите <code>{escape(bot_name)} название трека</code>.",
        [
            [{"text": "🔎 Найти трек", "switch_inline_query_current_chat": "", "style": "primary"}],
            [{"text": "📚 Мои плейлисты", "callback_data": "menu:playlists", "style": "success"}],
            [{"text": "🔐 Сессия", "callback_data": "menu:session"},
             {"text": "❔ Как пользоваться", "callback_data": "menu:help"}],
            [{"text": "ℹ️ О проекте", "callback_data": "menu:about"}],
        ],
    )


def help_page(username: str | None) -> tuple[str, ButtonRows]:
    bot_name = f"@{username}" if username else "@имя_бота"
    return (
        "<b>❔ Как пользоваться Iridium</b>\n\n"
        "1. Подключите свою сессию Apple Music в разделе «Сессия».\n"
        "2. Отправьте сюда название трека или ссылку music.apple.com.\n"
        f"3. Для поиска в любом чате введите <code>{escape(bot_name)} название трека</code>.\n\n"
        "AAC доступен по умолчанию. ALAC показывается владельцу только после включения в настройках.",
        [[{"text": "🔎 Найти трек", "switch_inline_query_current_chat": "", "style": "primary"}],
         [{"text": "← В меню", "callback_data": "menu:home"}]],
    )


def about_page(settings: Settings) -> tuple[str, ButtonRows]:
    creator = escape(settings.creator_name or "не указан")
    buttons: ButtonRows = []
    if settings.project_github_url:
        buttons.append([{"text": "↗ GitHub", "url": settings.project_github_url, "style": "primary"}])
    if settings.creator_url:
        buttons.append([{"text": "↗ Канал автора", "url": settings.creator_url}])
    buttons.append([{"text": "← В меню", "callback_data": "menu:home"}])
    return (
        "<b>ℹ️ О проекте Iridium</b>\n\n"
        "Личный Telegram-бот для поиска и загрузки треков Apple Music. "
        "У каждого пользователя своя сессия; AAC доступен по умолчанию, ALAC включается отдельно.\n\n"
        f"Создатель: <b>{creator}</b>\n"
        "Основа: aiogram 3.x и gamdl.",
        buttons,
    )


def session_page(connected: bool, uploaded: bool, owner_fallback: bool) -> tuple[str, ButtonRows]:
    status = "🟢 Подключена" if connected else "🟡 Не подключена"
    note = "\nФайл владельца также задан в .env." if owner_fallback else ""
    buttons: ButtonRows = [[{
        "text": "🔑 Подключить / обновить", "callback_data": "menu:connect", "style": "success",
    }]]
    if uploaded:
        buttons.append([{"text": "Удалить сессию", "callback_data": "menu:disconnect_confirm", "style": "danger"}])
    buttons.append([{"text": "← В меню", "callback_data": "menu:home"}])
    return (
        "<b>🔐 Ваша сессия Apple Music</b>\n\n"
        f"Статус: {status}\n"
        "Сессия привязана к вашему Telegram ID. Файл cookies даёт доступ к аккаунту — храните его в секрете."
        + note,
        buttons,
    )
