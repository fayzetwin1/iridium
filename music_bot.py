"""Compatibility entry point for Iridium; implementation lives in iridium_bot."""

from iridium_bot.app import Bot
from iridium_bot.catalog import AppleCatalog, Track, parse_apple_url
from iridium_bot.downloader import download_command, probe_codec, run_download, wrapper_ready
from iridium_bot.errors import CatalogError, DownloadError
from iridium_bot.main import main
from iridium_bot.settings import Settings
from iridium_bot.telegram import Telegram

if __name__ == "__main__":
    main()
