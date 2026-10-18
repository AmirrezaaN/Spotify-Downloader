"""Reads Spotify metadata without the Web API.

Since February 2026 the Web API needs a Premium account, so this module reads the public
embed player (__NEXT_DATA__ JSON) and the Open Graph / music:* meta tags that
open.spotify.com serves to link-preview crawlers. Embedded playlists list at most 100 tracks.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
from functools import lru_cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import settings
from .models import Collection, Track

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# open.spotify.com only renders the meta tags for non-browser clients.
PREVIEW_UA = "Mozilla/5.0 (compatible; LinkPreview/1.0)"

LINK_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?(?:embed/)?|spotify:)"
    r"(track|album|playlist)[/:]([A-Za-z0-9]{22})",
    re.IGNORECASE,
)
SHORT_LINK_RE = re.compile(r"https?://(?:spotify\.link|spotify\.app\.link)/[A-Za-z0-9_-]+", re.IGNORECASE)
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
META_RE = re.compile(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', re.S)
ALBUM_COVER_RE = re.compile(r"(ab67616d)[0-9a-f]{8}")


class SpotifyError(Exception):
    """An error whose message can be shown to the user as is."""


def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    if settings.proxies:
        s.proxies.update(settings.proxies)
    return s


session = _make_session()


def parse_link(text: str) -> tuple[str, str] | None:
    """Return (kind, id) for the first Spotify link found in text."""
    m = LINK_RE.search(text or "")
    if m:
        return m.group(1).lower(), m.group(2)
    short = SHORT_LINK_RE.search(text or "")
    if short:
        try:
            r = session.get(short.group(0), headers={"User-Agent": PREVIEW_UA}, timeout=15, allow_redirects=True)
        except requests.RequestException:
            return None
        m = LINK_RE.search(r.url) or LINK_RE.search(r.text)
        if m:
            return m.group(1).lower(), m.group(2)
    return None


def hires_cover(url: str | None) -> str | None:
    """Album art is also served at a larger size (usually 1000-1400 px) under another size code."""
    if not url:
        return None
    return ALBUM_COVER_RE.sub(r"\g<1>000082c1", url, count=1)


def _get(url: str, ua: str) -> str:
    try:
        r = session.get(url, headers={"User-Agent": ua, "Accept-Language": "en"}, timeout=20)
    except requests.RequestException as exc:
        raise SpotifyError("اتصال به اسپاتیفای برقرار نشد. اینترنت یا پروکسی رو چک کن.") from exc
    if r.status_code == 404:
        raise SpotifyError("این لینک پیدا نشد. شاید پلی‌لیست خصوصی باشه یا لینک اشتباهه.")
    if r.status_code >= 400:
        raise SpotifyError(f"اسپاتیفای خطا داد (کد {r.status_code}). کمی بعد دوباره امتحان کن.")
    r.encoding = "utf-8"
    return r.text


def _embed(kind: str, id_: str) -> dict:
    page = _get(f"https://open.spotify.com/embed/{kind}/{id_}", BROWSER_UA)
    m = NEXT_DATA_RE.search(page)
    if not m:
        raise SpotifyError("اطلاعات این لینک از اسپاتیفای خونده نشد.")
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SpotifyError("این لینک پیدا نشد. شاید پلی‌لیست خصوصی باشه یا لینک اشتباهه.") from exc
    return entity


def _meta(kind: str, id_: str) -> list[tuple[str, str]]:
    page = _get(f"https://open.spotify.com/{kind}/{id_}", PREVIEW_UA)
    return [(k, html.unescape(v)) for k, v in META_RE.findall(page)]


def _first(meta: list[tuple[str, str]], key: str) -> str | None:
    return next((v for k, v in meta if k == key), None)


def _split_artists(subtitle: str) -> list[str]:
    return [a.strip() for a in subtitle.replace("\xa0", " ").split(",") if a.strip()]


def _track_id(uri: str) -> str:
    return uri.rsplit(":", 1)[-1]


def _color(entity: dict) -> str | None:
    base = (entity.get("visualIdentity") or {}).get("backgroundBase")
    if not base:
        return None
    return "#{:02x}{:02x}{:02x}".format(base.get("red", 0), base.get("green", 0), base.get("blue", 0))


def _best_image(entity: dict) -> str | None:
    images = (entity.get("visualIdentity") or {}).get("image") or []
    if images:
        return max(images, key=lambda i: i.get("maxWidth") or 0)["url"]
    sources = (entity.get("coverArt") or {}).get("sources") or []
    return sources[0]["url"] if sources else None


@lru_cache(maxsize=512)
def _album_info(album_id: str) -> dict:
    """Album name, artist, date and per-track disc/track numbers from the album page."""
    meta = _meta("album", album_id)
    numbers: dict[str, tuple[int, int]] = {}
    current: str | None = None
    disc = 1
    for key, value in meta:
        if key == "music:song":
            current = value.rsplit("/", 1)[-1]
            disc = 1
        elif key == "music:song:disc" and current:
            disc = int(value or 1)
        elif key == "music:song:track" and current:
            numbers[current] = (disc, int(value or 0))
    count = _first(meta, "music:song_count")
    # og:title is "Listen to <album> - Album by <artist> | Spotify",
    # og:description is "<artist> · album · 1987 · 10 songs".
    title = _first(meta, "og:title") or ""
    m = re.match(r"^(?:Listen to )?(.*?) - (?:Album|Single|EP|Compilation) by .*?(?: \| Spotify)?$", title)
    desc = (_first(meta, "og:description") or "").split(" · ")
    return {
        "name": m.group(1) if m else title or None,
        "artist": desc[0] if len(desc) >= 3 else None,
        "release_date": _first(meta, "music:release_date"),
        "total": int(count) if count and count.isdigit() else len(numbers) or None,
        "numbers": numbers,
    }


def get_track(track_id: str) -> Track:
    return _load_track(track_id)[0]


def _load_track(track_id: str) -> tuple[Track, dict]:
    e = _embed("track", track_id)
    meta = _meta("track", track_id)

    album = album_id = None
    desc = _first(meta, "og:description") or ""
    parts = desc.split(" · ")
    if len(parts) >= 4:  # "Artist · Album · Song · 1987"
        album = " · ".join(parts[1:-2])
    album_url = _first(meta, "music:album")
    if album_url:
        album_id = album_url.rstrip("/").rsplit("/", 1)[-1]

    track_no = _first(meta, "music:album:track")
    release = _first(meta, "music:release_date") or ((e.get("releaseDate") or {}).get("isoString") or "")[:10]

    track = Track(
        id=track_id,
        title=e.get("title") or e.get("name") or "",
        artists=[a["name"] for a in e.get("artists") or []] or [p for p in parts[:1] if p],
        duration_ms=int(e.get("duration") or 0),
        album=album,
        album_id=album_id,
        release_date=release or None,
        track_number=int(track_no) if track_no and track_no.isdigit() else None,
        explicit=bool(e.get("isExplicit")),
        cover_url=_first(meta, "og:image") or _best_image(e),
    )
    if album_id:
        try:
            info = _album_info(album_id)
        except SpotifyError:
            info = None
        if info:
            track.album = track.album or info["name"]
            track.album_artist = info["artist"]
            track.total_tracks = info["total"]
            if track_id in info["numbers"]:
                track.disc_number, track.track_number = info["numbers"][track_id]
    return track, e


def enrich(track: Track) -> Track:
    """Fill in album, cover and numbering for a track that came from a playlist."""
    if not track.partial:
        return track
    full = get_track(track.id)
    full.partial = False
    return full


def _collection_tracks(entity: dict, **extra) -> list[Track]:
    tracks = []
    for item in entity.get("trackList") or []:
        if item.get("entityType", "track") != "track" or not item.get("uri", "").startswith("spotify:track:"):
            continue  # podcast episodes and local files can't be downloaded
        tracks.append(
            Track(
                id=_track_id(item["uri"]),
                title=item.get("title") or "",
                artists=_split_artists(item.get("subtitle") or ""),
                duration_ms=int(item.get("duration") or 0),
                explicit=bool(item.get("isExplicit")),
                **extra,
            )
        )
    return tracks


def get_collection(kind: str, id_: str) -> Collection:
    if kind == "track":
        t, entity = _load_track(id_)
        return Collection(
            kind="track", id=id_, title=t.title, subtitle=t.artist, cover_url=t.cover_url,
            tracks=[t], total=1, color=_color(entity),
        )

    entity = _embed(kind, id_)
    cover = _best_image(entity)

    if kind == "album":
        info = _album_info(id_)
        tracks = _collection_tracks(
            entity, album=entity.get("title"), album_id=id_, album_artist=entity.get("subtitle"),
            release_date=info["release_date"], cover_url=cover,
        )
        for i, t in enumerate(tracks, 1):
            t.disc_number, t.track_number = info["numbers"].get(t.id, (1, i))
            t.total_tracks = info["total"] or len(tracks)
        return Collection(
            kind="album", id=id_, title=entity.get("title") or "", subtitle=entity.get("subtitle") or "",
            cover_url=cover, tracks=tracks, total=max(len(tracks), info["total"] or 0), color=_color(entity),
        )

    tracks = _collection_tracks(entity, partial=True)
    total = len(tracks)
    try:
        count = _first(_meta("playlist", id_), "music:song_count")
        total = max(total, int(count)) if count and count.isdigit() else total
    except SpotifyError:
        pass
    return Collection(
        kind="playlist", id=id_, title=entity.get("title") or "", subtitle=entity.get("subtitle") or "",
        cover_url=cover, tracks=tracks[: settings.playlist_limit], total=total, color=_color(entity),
    )


_resolve_cache: dict[tuple[str, str], tuple[float, Collection]] = {}
_resolve_lock = threading.Lock()
RESOLVE_TTL = 15 * 60


def resolve(text: str) -> Collection:
    """Turn any Spotify link (or text containing one) into a Collection. Results are cached briefly."""
    parsed = parse_link(text)
    if not parsed:
        raise SpotifyError("لینک اسپاتیفای معتبر نیست. لینک یک ترک، آلبوم یا پلی‌لیست بفرست.")
    now = time.time()
    with _resolve_lock:
        hit = _resolve_cache.get(parsed)
        if hit and now - hit[0] < RESOLVE_TTL:
            return hit[1]
    collection = get_collection(*parsed)
    if not collection.tracks:
        raise SpotifyError("هیچ ترک قابل دانلودی توی این لینک نیست.")
    with _resolve_lock:
        _resolve_cache[parsed] = (now, collection)
    return collection
