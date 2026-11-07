from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def optional_https_url(value: str, field: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{field} must be an https URL")
    return value

@dataclass(frozen=True)
class Settings:
    bot_token: str
    user_id: int
    cookies_path: Path
    enable_alac: bool = False
    wrapper_url: str = "http://127.0.0.1:80"
    wrapper_decrypt_host: str = "127.0.0.1"
    wrapper_decrypt_port: int = 10020
    allowed_user_ids: frozenset[int] = frozenset()
    session_dir: Path = Path("sessions")
    project_github_url: str | None = "https://github.com/fayzetwin1/iridium"
    creator_name: str | None = "FayzeTwin"
    creator_url: str | None = "https://t.me/fayzetwintop"

    def is_allowed(self, user_id: int) -> bool:
        return user_id == self.user_id or user_id in self.allowed_user_ids

    @classmethod
    def from_env(cls) -> Settings:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        user = os.getenv("TELEGRAM_USER_ID", "").strip()
        cookies = os.getenv("APPLE_MUSIC_COOKIES_PATH", "").strip()
        if not token or not user.isdecimal() or not cookies:
            raise ValueError(
                "Set TELEGRAM_BOT_TOKEN, numeric TELEGRAM_USER_ID, "
                "and APPLE_MUSIC_COOKIES_PATH."
            )
        cookie_path = Path(cookies).expanduser()
        if not cookie_path.is_file():
            raise ValueError(f"Cookies file not found: {cookie_path}")
        extra_users = os.getenv("TELEGRAM_USER_IDS", "").strip()
        user_ids = [item.strip() for item in extra_users.split(",") if item.strip()]
        if any(not item.isdecimal() for item in user_ids):
            raise ValueError("TELEGRAM_USER_IDS must be comma-separated numeric IDs")
        return cls(
            bot_token=token,
            user_id=int(user),
            cookies_path=cookie_path,
            enable_alac=os.getenv("ENABLE_ALAC", "false").lower() in {"1", "true", "yes"},
            wrapper_url=os.getenv("WRAPPER_URL", "http://127.0.0.1:80"),
            wrapper_decrypt_host=os.getenv("WRAPPER_DECRYPT_HOST", "127.0.0.1"),
            wrapper_decrypt_port=int(os.getenv("WRAPPER_DECRYPT_PORT", "10020")),
            allowed_user_ids=frozenset(int(item) for item in user_ids),
            session_dir=Path(os.getenv("SESSION_DIR", "sessions")).expanduser(),
            project_github_url=optional_https_url(os.getenv("PROJECT_GITHUB_URL", "https://github.com/fayzetwin1/iridium"), "PROJECT_GITHUB_URL"),
            creator_name=os.getenv("PROJECT_CREATOR", "FayzeTwin").strip() or None,
            creator_url=optional_https_url(os.getenv("PROJECT_CREATOR_URL", "https://t.me/fayzetwintop"), "PROJECT_CREATOR_URL"),
        )
