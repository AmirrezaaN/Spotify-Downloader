"""Turns a Spotify track and a chosen quality into a finished, fully tagged file (cached)."""
from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import requests
from mutagen import File as MutagenFile
from mutagen.mp3 import BitrateMode
from PIL import Image

from . import spotify
from .cache import CacheEntry, cache
from .config import settings
from .lyrics import fetch_lyrics
from .models import Track
from .qualities import QUALITIES, Quality, is_available
from .sources import RawAudio, SourceNotFound, enabled_sources
from .tagger import tag_file

log = logging.getLogger(__name__)


class DownloadError(Exception):
    """A failure whose message can be shown to the user."""


@dataclass
class DownloadResult:
    entry: CacheEntry
    track: Track
    from_cache: bool

    @property
    def path(self) -> Path:
        return self.entry.path

    @property
    def filename(self) -> str:
        return self.entry.filename

    @property
    def info(self) -> dict:
        return self.entry.info


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def safe_filename(track: Track, ext: str) -> str:
    name = f"{track.artist} - {track.title}"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip(" .")
    return f"{name[:150] or track.id}{ext}"


def lookup(track_id: str, quality: str) -> CacheEntry | None:
    return cache.get(cache.make_key(track_id, quality))


# ---------- cover art ----------

def get_cover(track: Track) -> bytes | None:
    if not track.cover_url:
        return None
    cache_name = settings.covers_dir / f"{track.album_id or track.id}.jpg"
    if cache_name.exists():
        return cache_name.read_bytes()
    for url in dict.fromkeys([spotify.hires_cover(track.cover_url), track.cover_url]):
        try:
            r = requests.get(url, timeout=20, proxies=settings.proxies)
            if r.ok and r.content:
                cache_name.write_bytes(r.content)
                return r.content
        except requests.RequestException:
            continue
    return None


def make_thumbnail(cover: bytes | None, size: int = 320) -> bytes | None:
    """Telegram wants a JPEG thumbnail of at most 320x320 and 200 KB."""
    if not cover:
        return None
    try:
        img = Image.open(io.BytesIO(cover)).convert("RGB")
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except OSError:
        return None


# ---------- audio ----------

def _fetch_raw(track: Track, quality: Quality, workdir: Path) -> RawAudio:
    tried = False
    for tier in quality.tiers:
        for source in enabled_sources():
            if tier not in source.tiers:
                continue
            tried = True
            try:
                return source.fetch(track, tier, workdir)
            except SourceNotFound as exc:
                log.info("%s: %s", source.name, exc)
            except Exception as exc:  # network errors, yt-dlp errors, ...
                log.warning("%s failed for %s: %s", source.name, track.id, exc)
    if not tried:
        raise DownloadError("این کیفیت فعلاً در دسترس نیست.")
    raise DownloadError("این ترک توی منابع پیدا نشد یا دانلودش ناموفق بود.")


def _ffmpeg(src: Path, out: Path, args: list[str]) -> Path:
    cmd = [settings.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vn", "-map", "0:a:0", "-map_metadata", "-1", *args, str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        log.error("ffmpeg failed: %s", proc.stderr[-800:])
        raise DownloadError("تبدیل فایل ناموفق بود (ffmpeg).")
    return out


def _convert(raw: RawAudio, quality: Quality, workdir: Path) -> Path:
    src = raw.path
    mp3 = workdir / "out.mp3"
    copy = ["-c:a", "copy"]

    if quality.key == "mp3_128":
        return _ffmpeg(src, mp3, ["-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100"])

    if quality.key == "best_mp3":
        if raw.codec == "mp3":
            return _ffmpeg(src, mp3, copy)
        if raw.lossless:
            return _ffmpeg(src, mp3, ["-c:a", "libmp3lame", "-b:a", "320k"])
        # VBR V0 keeps everything a ~130-256 kbps lossy source has, without padding it to a fake 320.
        return _ffmpeg(src, mp3, ["-c:a", "libmp3lame", "-q:a", "0", "-ar", "44100"])

    if quality.key == "best_orig":
        if raw.codec == "opus":
            return _ffmpeg(src, workdir / "out.opus", copy)
        if raw.codec == "aac":
            return _ffmpeg(src, workdir / "out.m4a", [*copy, "-movflags", "+faststart"])
        if raw.codec == "mp3":
            return _ffmpeg(src, mp3, copy)
        if raw.codec == "flac":
            return _ffmpeg(src, workdir / "out.flac", copy)
        if raw.lossless:
            return _ffmpeg(src, workdir / "out.flac", ["-c:a", "flac"])
        return _ffmpeg(src, mp3, ["-c:a", "libmp3lame", "-q:a", "0"])

    if quality.key == "mp3_320":
        if raw.codec == "mp3" and (raw.bitrate_kbps or 0) >= 300:
            return _ffmpeg(src, mp3, copy)
        return _ffmpeg(src, mp3, ["-c:a", "libmp3lame", "-b:a", "320k"])

    if quality.key == "flac":
        if raw.codec == "flac":
            return _ffmpeg(src, workdir / "out.flac", copy)
        return _ffmpeg(src, workdir / "out.flac", ["-c:a", "flac"])

    raise DownloadError("کیفیت نامعتبر.")


def _output_label(path: Path) -> tuple[str, int | None, float | None]:
    audio = MutagenFile(path)
    fmt = {".mp3": "MP3", ".m4a": "AAC", ".opus": "Opus", ".flac": "FLAC"}[path.suffix]
    bitrate = round(audio.info.bitrate / 1000) if audio and getattr(audio.info, "bitrate", 0) else None
    length = audio.info.length if audio else None
    if fmt == "FLAC":
        bits = getattr(audio.info, "bits_per_sample", 16)
        rate = getattr(audio.info, "sample_rate", 44100) / 1000
        return f"FLAC · {bits}bit/{rate:g}kHz", bitrate, length
    vbr = fmt == "MP3" and getattr(audio.info, "bitrate_mode", None) == BitrateMode.VBR
    return f"{fmt} · {'VBR ~' if vbr else ''}{bitrate}kbps", bitrate, length


def download(track: Track, quality_key: str) -> DownloadResult:
    """Download, convert and tag one track. Concurrent calls for the same file wait for each other."""
    quality = QUALITIES.get(quality_key)
    if not quality or not is_available(quality_key):
        raise DownloadError("این کیفیت فعلاً در دسترس نیست.")

    key = cache.make_key(track.id, quality_key)
    with _lock_for(key):
        hit = cache.get(key)
        if hit and hit.has_file:
            return DownloadResult(hit, track, from_cache=True)

        try:
            track = spotify.enrich(track)
        except spotify.SpotifyError as exc:
            raise DownloadError(str(exc)) from exc

        workdir = Path(tempfile.mkdtemp(dir=settings.tmp_dir))
        try:
            raw = _fetch_raw(track, quality, workdir)
            out = _convert(raw, quality, workdir)
            cover = get_cover(track)
            lyrics = fetch_lyrics(track)
            comment = f"{settings.app_name} · {raw.description} · {track.url}"
            tag_file(out, track, cover, lyrics.best if lyrics else None, comment)

            label, bitrate, length = _output_label(out)
            final = settings.library_dir / f"{track.id}_{quality_key}{out.suffix}"
            shutil.move(str(out), final)
            entry = CacheEntry(
                key=key, track_id=track.id, quality=quality_key, path=final,
                filename=safe_filename(track, final.suffix), size=final.stat().st_size,
                info={
                    "label": label,
                    "quality_label": quality.label,
                    "bitrate_kbps": bitrate,
                    "duration": round(length) if length else track.duration_s,
                    "source": raw.description,
                    "source_url": raw.source_url,
                    "lyrics": None if not lyrics else ("synced" if lyrics.synced else "plain"),
                    "track": track.to_dict(),
                },
                tg_file_id=hit.tg_file_id if hit else None,
            )
            cache.put(entry)
            return DownloadResult(entry, track, from_cache=False)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def check_environment() -> list[str]:
    """Problems that will stop downloads, worded for the person running the app."""
    problems = []
    if not shutil.which(settings.ffmpeg_path) and not Path(settings.ffmpeg_path).exists():
        problems.append("ffmpeg not found. Install it or set FFMPEG_PATH in .env.")
    if not settings.deno_path:
        problems.append("Deno not found (needed by yt-dlp for YouTube). Run: pip install deno")
    return problems
