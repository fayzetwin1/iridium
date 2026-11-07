"""Export one Apple Music session cookie after an interactive local browser login.

This does not read an existing browser profile, collect passwords, or upload data.
It creates only the Netscape cookie needed by gamdl's cookie authentication.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path


APPLE_MUSIC_URL = "https://music.apple.com/"
COOKIE_NAME = "media-user-token"
MAX_UPLOAD_BYTES = 64 * 1024


def default_output_path() -> Path:
    """Put an exported cookie beside the standalone executable when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "cookies.txt"
    return Path("cookies.txt")


def find_music_cookie(cookies: list[dict]) -> dict | None:
    """Return the Apple Music token cookie, ignoring unrelated browser cookies."""
    for cookie in cookies:
        if (
            cookie.get("name") == COOKIE_NAME
            and str(cookie.get("domain", "")).lstrip(".") == "music.apple.com"
            and cookie.get("value")
        ):
            return cookie
    return None


def read_netscape_cookie(data: bytes) -> dict:
    """Extract one music token from a user-supplied Netscape export."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Cookie file is too large")
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Cookie file is not UTF-8 text") from exc
    for line in lines:
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        fields = line.split("\t", 6)
        if len(fields) != 7:
            continue
        domain, _, _, _, expires, name, value = fields
        if domain.lstrip(".") != "music.apple.com" or name != COOKIE_NAME or not value:
            continue
        try:
            expiration = int(expires)
        except ValueError as exc:
            raise ValueError("Invalid cookie expiration") from exc
        return {"name": COOKIE_NAME, "domain": ".music.apple.com", "value": value, "expires": expiration}
    raise ValueError("media-user-token for music.apple.com was not found")


def netscape_cookie(cookie: dict) -> str:
    """Use the exact domain expected by gamdl's MozillaCookieJar reader."""
    value = str(cookie["value"])
    if any(character in value for character in "\r\n\t\0"):
        raise ValueError("Cookie contains an invalid control character")
    expires = max(0, int(cookie.get("expires") or 0))
    return (
        "# Netscape HTTP Cookie File\n"
        "# Apple Music session cookie for gamdl. Keep this file private.\n"
        f".music.apple.com\tTRUE\t/\tTRUE\t{expires}\t{COOKIE_NAME}\t{value}\n"
    )


def write_cookie(path: Path, cookie: dict, replace: bool = False) -> Path:
    path = path.expanduser().resolve()
    if not path.parent.is_dir():
        raise FileNotFoundError(f"Output directory does not exist: {path.parent}")
    if path.exists() and not replace:
        raise FileExistsError(f"File already exists: {path} (use --replace to overwrite)")
    payload = netscape_cookie(cookie)
    fd, temporary = tempfile.mkstemp(prefix=".apple-music-cookie-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def export_cookie(output: Path, browser_name: str, timeout: int, replace: bool) -> Path:
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    channel = None if browser_name == "chromium" else browser_name
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel=channel, headless=False)
        except PlaywrightError as exc:
            raise RuntimeError(
                f"Не удалось открыть {browser_name}. Проверьте установку браузера; "
                "для Chromium выполните: python -m playwright install chromium"
            ) from exc
        try:
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto(APPLE_MUSIC_URL, wait_until="domcontentloaded")
            except PlaywrightError as exc:
                raise RuntimeError("Не удалось открыть music.apple.com в браузере.") from exc
            print("Войдите в Apple Music в открывшемся окне. Ожидаю завершения входа…")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if page.is_closed():
                    raise RuntimeError("Окно закрыто до завершения входа.")
                try:
                    cookie = find_music_cookie(context.cookies(APPLE_MUSIC_URL))
                except PlaywrightError as exc:
                    raise RuntimeError("Окно браузера закрылось до завершения входа.") from exc
                if cookie:
                    return write_cookie(output, cookie, replace)
                time.sleep(1)
            raise TimeoutError("Время ожидания входа истекло; файл не создан.")
        finally:
            browser.close()


def main() -> None:
    try:
        run_cli()
    finally:
        if getattr(sys, "frozen", False) and not {"-h", "--help", "--self-test"}.intersection(sys.argv) and sys.stdin.isatty():
            try:
                input("Нажмите Enter, чтобы закрыть окно…")
            except (EOFError, KeyboardInterrupt):
                pass


def run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Log in to Apple Music locally and save gamdl's cookies.txt."
    )
    parser.add_argument("--output", type=Path, default=default_output_path())
    parser.add_argument(
        "--browser", choices=("msedge", "chrome", "chromium"),
        default="msedge" if sys.platform == "win32" else "chromium",
    )
    parser.add_argument("--timeout", type=int, default=600, help="Login wait in seconds")
    parser.add_argument("--replace", action="store_true", help="Replace an existing cookie file")
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_test:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            print(f"Playwright driver ready: {playwright.chromium.name}")
        return
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    try:
        output = export_cookie(args.output, args.browser, args.timeout, args.replace)
    except (FileExistsError, FileNotFoundError, RuntimeError, TimeoutError, ValueError) as exc:
        parser.exit(1, f"Ошибка: {exc}\n")
    print(f"Готово: {output}")
    print("Храните этот файл как пароль. Скрипт ничего не отправлял в Telegram.")


if __name__ == "__main__":
    main()
