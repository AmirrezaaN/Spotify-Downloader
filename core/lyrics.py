"""Lyrics from LRCLIB (free, no key), synced LRC when available."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from .config import settings
from .models import Track

log = logging.getLogger(__name__)
API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "SpotiLoad/1.0 (personal music downloader)"}


@dataclass
class Lyrics:
    plain: str | None
    synced: str | None

    @property
    def best(self) -> str | None:
        if settings.lyrics_style == "plain":
            return self.plain or self.synced
        return self.synced or self.plain


def _get(path: str, params: dict):
    for attempt in range(3):
        r = requests.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=12, proxies=settings.proxies)
        if r.status_code < 500 or attempt == 2:
            break
        time.sleep(1.5 * (attempt + 1))
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def fetch_lyrics(track: Track) -> Lyrics | None:
    if not settings.lyrics_enabled:
        return None
    try:
        data = _get("get", {
            "track_name": track.title,
            "artist_name": track.main_artist,
            "album_name": track.album or "",
            "duration": track.duration_s,
        })
        if not data:
            results = _get("search", {"track_name": track.title, "artist_name": track.main_artist}) or []
            results = [r for r in results if abs((r.get("duration") or 0) - track.duration_s) <= 5]
            data = min(results, key=lambda r: abs(r["duration"] - track.duration_s), default=None)
    except (requests.RequestException, ValueError) as exc:
        log.info("lyrics lookup failed for %s: %s", track.title, exc)
        return None
    if not data or data.get("instrumental"):
        return None
    lyrics = Lyrics(plain=data.get("plainLyrics") or None, synced=data.get("syncedLyrics") or None)
    return lyrics if lyrics.best else None
