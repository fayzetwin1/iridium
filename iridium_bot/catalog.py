from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .constants import MAX_RESULTS
from .errors import CatalogError

LOG = logging.getLogger("music_bot")

@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    url: str
    library_id: str = ""

    @classmethod
    def from_resource(cls, item: dict) -> Track | None:
        attributes = item.get("attributes") or {}
        url = attributes.get("url")
        if not url:
            catalog = ((item.get("relationships") or {}).get("catalog") or {}).get("data") or []
            if catalog:
                url = (catalog[0].get("attributes") or {}).get("url")
        parsed = parse_apple_url(url) if url else None
        if parsed and parsed[0] == "song":
            return cls(attributes.get("name") or "Без названия", attributes.get("artistName") or "", url)
        if item.get("type") == "library-songs" and item.get("id"):
            return cls(attributes.get("name") or "Без названия", attributes.get("artistName") or "", "", item["id"])
        return None


def parse_apple_url(value: str) -> tuple[str, str] | None:
    """Accept only Apple Music song and playlist URLs, never arbitrary download URLs."""
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or parsed.hostname != "music.apple.com":
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 3:
            return None
        item_id = parts[-1]
        song_id = parse_qs(parsed.query).get("i", [None])[0]
        if song_id and song_id.isdecimal() and "album" in parts:
            return "song", song_id
        if "song" in parts and item_id.isdecimal():
            return "song", item_id
        if "library" in parts and "songs" in parts and item_id.startswith(("i.", "l.", "p.")):
            return "library_song", item_id
        if "playlist" in parts and (item_id.startswith("pl.") or item_id.startswith("p.")):
            return "playlist", item_id
    except (ValueError, IndexError):
        pass
    return None


def first_resource(payload: dict) -> dict | None:
    data = payload.get("data") or []
    return data[0] if data else None


class AppleCatalog:
    def __init__(self, cookies_path: Path):
        self.cookies_path = cookies_path

    async def _with_api(self, operation):
        from gamdl.api import AppleMusicApi
        from gamdl.api.exceptions import GamdlApiResponseError

        try:
            api = await AppleMusicApi.create_from_netscape_cookies(str(self.cookies_path))
        except ValueError as exc:
            raise CatalogError("В cookies нет действующей сессии Apple Music.") from exc
        except GamdlApiResponseError as exc:
            raise CatalogError("Не удалось войти в Apple Music. Проверьте cookies и подписку.") from exc
        try:
            if not api.active_subscription:
                raise CatalogError("Подписка Apple Music неактивна или сессия истекла.")
            try:
                return await operation(api)
            except GamdlApiResponseError as exc:
                if exc.status_code in {401, 403}:
                    raise CatalogError("Сессия Apple Music истекла или доступ запрещён.") from exc
                if exc.status_code == 404:
                    raise CatalogError("Трек или плейлист недоступен в вашем регионе.") from exc
                raise CatalogError("Apple Music вернул ошибку. Повторите позже.") from exc
        finally:
            await api.client.aclose()

    async def search(self, query: str) -> list[Track]:
        async def operation(api):
            result = await api.get_search_results(query, types="songs", limit=MAX_RESULTS)
            items = (((result.get("results") or {}).get("songs") or {}).get("data") or [])
            return [track for item in items if (track := Track.from_resource(item))]

        return await self._with_api(operation)

    async def song(self, song_id: str) -> Track | None:
        async def operation(api):
            return Track.from_resource(first_resource(await api.get_song(song_id)) or {})

        return await self._with_api(operation)

    async def library_song(self, song_id: str) -> Track | None:
        async def operation(api):
            return Track.from_resource(first_resource(await api.get_library_song(song_id)) or {})

        return await self._with_api(operation)

    async def playlist(self, playlist_id: str) -> tuple[str, list[Track]]:
        async def operation(api):
            library = playlist_id.startswith("p.")
            result = (
                await api.get_library_playlist(playlist_id, limit=100)
                if library
                else await api.get_playlist(playlist_id, limit_tracks=100)
            )
            playlist = first_resource(result) or {}
            title = (playlist.get("attributes") or {}).get("name") or "Плейлист"
            relation = ((playlist.get("relationships") or {}).get("tracks") or {})
            items = list(relation.get("data") or [])
            next_page = relation.get("next")
            pages = 0
            while next_page and pages < 19:
                if not next_page.startswith("/v1/"):
                    break
                page = await api._amp_request(next_page)
                items.extend(page.get("data") or [])
                next_page = page.get("next")
                pages += 1
            tracks = [track for item in items if (track := Track.from_resource(item))]
            if next_page:
                LOG.warning("Playlist track cap reached")
            return title, tracks

        return await self._with_api(operation)

    async def library_playlists(self) -> list[dict]:
        async def operation(api):
            result = await api.get_library_playlists(limit=100)
            items = list(result.get("data") or [])
            next_page = result.get("next")
            pages = 0
            while next_page and pages < 19:
                if not next_page.startswith("/v1/"):
                    break
                page = await api._amp_request(next_page)
                items.extend(page.get("data") or [])
                next_page = page.get("next")
                pages += 1
            return items

        return await self._with_api(operation)

    async def resolve_library_track(self, track: Track) -> Track | None:
        if track.url:
            return track

        async def operation(api):
            item = first_resource(await api.get_library_song(track.library_id)) or {}
            resolved = Track.from_resource(item)
            return resolved if resolved and resolved.url else None

        return await self._with_api(operation)
