"""YouTube Music source: free, no account. Real quality is Opus ~130 kbps (AAC 256 kbps with Premium cookies)."""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from pathlib import Path

import yt_dlp
from rapidfuzz import fuzz
from ytmusicapi import YTMusic

from ..config import settings
from ..models import Track
from .base import AudioSource, RawAudio, SourceNotFound

log = logging.getLogger(__name__)

FEAT_RE = re.compile(r"[(\[](?:feat|ft|with|prod)\.?\s[^)\]]*[)\]]", re.I)
SUFFIX_RE = re.compile(r"\s+-\s+.*(?:remaster|version|edit|mono|stereo|from\s).*$", re.I)
# Words that mean a different recording when they show up only in the candidate's title.
VARIANT_WORDS = ("live", "remix", "cover", "karaoke", "instrumental", "sped up", "slowed", "reverb",
                 "nightcore", "8d", "acoustic", "piano version", "extended", "mashup")

SONG_THRESHOLD = 65
VIDEO_THRESHOLD = 72


def _norm(text: str) -> str:
    text = FEAT_RE.sub(" ", text or "")
    text = SUFFIX_RE.sub("", text)
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(track: Track, cand: dict, is_video: bool) -> float:
    duration = cand.get("duration_seconds")
    if duration:
        diff = abs(duration - track.duration_s)
        if diff > 20:
            return 0
        duration_score = max(0.0, 100 - diff * 5)
    else:
        duration_score = 40.0

    title, cand_title = _norm(track.title), _norm(cand.get("title", ""))
    title_score = fuzz.token_set_ratio(title, cand_title)

    cand_artists = _norm(" ".join(a.get("name", "") for a in cand.get("artists") or []))
    artist_score = 0.0
    for artist in track.artists:
        a = _norm(artist)
        artist_score = max(artist_score, fuzz.partial_ratio(a, cand_artists) if cand_artists else 0)
        if is_video:  # video titles are usually "Artist - Title"
            artist_score = max(artist_score, fuzz.partial_ratio(a, cand_title))

    score = 0.45 * title_score + 0.35 * artist_score + 0.20 * duration_score
    for word in VARIANT_WORDS:
        if re.search(rf"\b{word}\b", cand_title) and not re.search(rf"\b{word}\b", title):
            score -= 30
    album = (cand.get("album") or {}).get("name")
    if track.album and album and fuzz.ratio(_norm(track.album), _norm(album)) > 90:
        score += 5
    return score


class _QuietLogger:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        log.debug("yt-dlp: %s", msg)

    def error(self, msg):
        log.warning("yt-dlp: %s", msg)


class YouTubeMusicSource(AudioSource):
    name = "youtube"
    title = "YouTube Music"
    tiers = frozenset({"lossy"})

    def __init__(self) -> None:
        self._local = threading.local()

    @property
    def _yt(self) -> YTMusic:
        # ytmusicapi keeps a requests session per instance, so use one per thread.
        if not hasattr(self._local, "yt"):
            self._local.yt = YTMusic(proxies=settings.proxies)
        return self._local.yt

    def find(self, track: Track) -> tuple[str, float]:
        """Return (videoId, score) of the best match."""
        queries = [f"{track.main_artist} {track.title}"]
        if len(track.artists) > 1:
            queries.append(f"{track.title} {track.artist}")
        best: tuple[str, float] | None = None

        for query in queries:
            for cand in self._yt.search(query, filter="songs", limit=10)[:10]:
                if cand.get("videoId"):
                    s = _score(track, cand, is_video=False)
                    if not best or s > best[1]:
                        best = (cand["videoId"], s)
            if best and best[1] >= SONG_THRESHOLD + 15:
                return best

        if best and best[1] >= SONG_THRESHOLD:
            return best

        for cand in self._yt.search(queries[0], filter="videos", limit=10)[:10]:
            if cand.get("videoId"):
                s = _score(track, cand, is_video=True)
                if s >= VIDEO_THRESHOLD and (not best or s > best[1]):
                    best = (cand["videoId"], s)
        if best and best[1] >= SONG_THRESHOLD:
            return best
        raise SourceNotFound(f"no YouTube Music match for {track.artist} - {track.title}")

    def fetch(self, track: Track, tier: str, workdir: Path) -> RawAudio:
        video_id, score = self.find(track)
        log.info("matched %s - %s -> %s (score %.0f)", track.artist, track.title, video_id, score)
        url = f"https://music.youtube.com/watch?v={video_id}"
        opts = {
            "format": "bestaudio/best",
            "outtmpl": str(workdir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "retries": 5,
            "fragment_retries": 5,
            "socket_timeout": 30,
            "logger": _QuietLogger(),
        }
        if settings.deno_path:
            opts["js_runtimes"] = {"deno": {"path": settings.deno_path}}
        if settings.proxy:
            opts["proxy"] = settings.proxy
        if settings.yt_cookies and settings.yt_cookies.exists():
            opts["cookiefile"] = str(settings.yt_cookies)

        # googlevideo occasionally answers 403 for a stream URL; a fresh extraction usually fixes it.
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    path = Path(ydl.prepare_filename(info))
                break
            except yt_dlp.utils.DownloadError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

        acodec = (info.get("acodec") or "").lower()
        codec = "aac" if acodec.startswith("mp4a") else acodec.split(".")[0] or "opus"
        return RawAudio(
            path=path,
            codec=codec,
            bitrate_kbps=info.get("abr"),
            sample_rate=info.get("asr"),
            lossless=False,
            source_title=self.title,
            source_url=f"https://music.youtube.com/watch?v={video_id}",
        )
