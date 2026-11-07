import logging

from .app import Bot
from .catalog import AppleCatalog
from .settings import Settings
from .telegram import Telegram

def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings.from_env()
    telegram = Telegram(settings.bot_token)
    bot = Bot(settings, telegram, AppleCatalog(settings.cookies_path))
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
