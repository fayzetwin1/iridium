from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx

from .errors import DownloadError
from .settings import Settings

LOG = logging.getLogger("music_bot")

def download_command(settings: Settings, url: str, quality: str, output: Path, temp: Path) -> list[str]:
    command = [
        sys.executable, "-m", "gamdl", "--no-config-file", "--no-synced-lyrics",
        "--cookies-path", str(settings.cookies_path),
        "--output-path", str(output), "--temp-path", str(temp),
        "--song-codec-priority", "alac" if quality == "alac" else "aac-web",
    ]
    if quality == "alac":
        command += [
            "--use-wrapper", "--wrapper-url", settings.wrapper_url,
            "--wrapper-decrypt-host", settings.wrapper_decrypt_host,
            "--wrapper-decrypt-port", str(settings.wrapper_decrypt_port),
        ]
    return command + [url]


async def wrapper_ready(settings: Settings) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(settings.wrapper_url.rstrip("/") + "/health")
        response.raise_for_status()
        return bool((response.json().get("runtime") or {}).get("playback_ready"))
    except (httpx.HTTPError, ValueError):
        return False


async def run_download(settings: Settings, url: str, quality: str, output: Path, temp: Path) -> Path:
    command = download_command(settings, url, quality, output, temp)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=600)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("Загрузка превысила 10 минут.")
    files = [p for p in output.rglob("*.m4a") if p.is_file()]
    if process.returncode != 0 or len(files) != 1:
        LOG.warning("gamdl failed with exit %s and %s output files", process.returncode, len(files))
        diagnostic = stdout.decode(errors="replace").lower()
        if "active apple music subscription" in diagnostic or "media-user-token" in diagnostic:
            raise DownloadError("Сессия Apple Music истекла или подписка неактивна.")
        if quality == "alac" and ("format not available" in diagnostic or "decryption not available" in diagnostic):
            raise DownloadError("ALAC недоступен для этого трека или wrapper не готов. Выберите AAC.")
        raise DownloadError("Не удалось получить трек. Проверьте доступность в вашем регионе.")
    codec = await probe_codec(files[0])
    expected = "alac" if quality == "alac" else "aac"
    if codec != expected:
        raise DownloadError(f"Получен кодек {codec or 'unknown'} вместо {expected}; файл не отправлен.")
    return files[0]


async def probe_codec(file: Path) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1",
            str(file), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise DownloadError("ffprobe не найден: он нужен для проверки AAC/ALAC.") from exc
    stdout, _ = await process.communicate()
    return stdout.decode(errors="replace").strip() if process.returncode == 0 else ""
