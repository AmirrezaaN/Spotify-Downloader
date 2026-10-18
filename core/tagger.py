"""Writes title, artists, album, numbering, cover art and lyrics into MP3, M4A, Opus and FLAC files."""
from __future__ import annotations

import base64
from pathlib import Path

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, COMM, ID3, TALB, TDRC, TPE1, TPE2, TPOS, TRCK, TIT2, TXXX, USLT, WOAS
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from .models import Track


def _number(n: int | None, total: int | None) -> str | None:
    if not n:
        return None
    return f"{n}/{total}" if total else str(n)


def _tag_mp3(path: Path, t: Track, cover: bytes | None, lyrics: str | None, comment: str) -> None:
    tags = ID3()
    tags.add(TIT2(encoding=3, text=t.title))
    tags.add(TPE1(encoding=3, text=t.artist))
    tags.add(TPE2(encoding=3, text=t.album_artist or t.main_artist))
    if t.album:
        tags.add(TALB(encoding=3, text=t.album))
    if t.release_date:
        tags.add(TDRC(encoding=3, text=t.release_date))
    if num := _number(t.track_number, t.total_tracks):
        tags.add(TRCK(encoding=3, text=num))
    if t.disc_number:
        tags.add(TPOS(encoding=3, text=str(t.disc_number)))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
    if lyrics:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=comment))
    tags.add(WOAS(url=t.url))
    tags.add(TXXX(encoding=3, desc="SPOTIFY_ID", text=t.id))
    # ID3v2.3 is what Windows Explorer, iTunes and most car stereos read reliably.
    tags.save(path, v2_version=3)


def _tag_m4a(path: Path, t: Track, cover: bytes | None, lyrics: str | None, comment: str) -> None:
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    else:
        audio.tags.clear()
    tags = audio.tags
    tags["\xa9nam"] = t.title
    tags["\xa9ART"] = t.artist
    tags["aART"] = t.album_artist or t.main_artist
    if t.album:
        tags["\xa9alb"] = t.album
    if t.release_date:
        tags["\xa9day"] = t.release_date
    if t.track_number:
        tags["trkn"] = [(t.track_number, t.total_tracks or 0)]
    if t.disc_number:
        tags["disk"] = [(t.disc_number, 0)]
    if cover:
        tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    if lyrics:
        tags["\xa9lyr"] = lyrics
    tags["\xa9cmt"] = comment
    tags["rtng"] = [1 if t.explicit else 0]
    audio.save()


def _vorbis_fields(t: Track, lyrics: str | None, comment: str) -> dict[str, list[str]]:
    fields = {
        "TITLE": [t.title],
        "ARTIST": t.artists,
        "ALBUMARTIST": [t.album_artist or t.main_artist],
        "COMMENT": [comment],
        "SPOTIFY_ID": [t.id],
        "WEBSITE": [t.url],
    }
    if t.album:
        fields["ALBUM"] = [t.album]
    if t.release_date:
        fields["DATE"] = [t.release_date]
    if t.track_number:
        fields["TRACKNUMBER"] = [str(t.track_number)]
    if t.total_tracks:
        fields["TRACKTOTAL"] = [str(t.total_tracks)]
    if t.disc_number:
        fields["DISCNUMBER"] = [str(t.disc_number)]
    if lyrics:
        fields["LYRICS"] = [lyrics]
    return fields


def _picture(cover: bytes) -> Picture:
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.desc = "Cover"
    pic.data = cover
    return pic


def _tag_opus(path: Path, t: Track, cover: bytes | None, lyrics: str | None, comment: str) -> None:
    audio = OggOpus(path)
    audio.delete()
    for key, value in _vorbis_fields(t, lyrics, comment).items():
        audio[key] = value
    if cover:
        audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(_picture(cover).write()).decode("ascii")]
    audio.save()


def _tag_flac(path: Path, t: Track, cover: bytes | None, lyrics: str | None, comment: str) -> None:
    audio = FLAC(path)
    audio.delete()
    audio.clear_pictures()
    for key, value in _vorbis_fields(t, lyrics, comment).items():
        audio[key] = value
    if cover:
        audio.add_picture(_picture(cover))
    audio.save()


TAGGERS = {".mp3": _tag_mp3, ".m4a": _tag_m4a, ".opus": _tag_opus, ".flac": _tag_flac}


def tag_file(path: Path, track: Track, cover: bytes | None, lyrics: str | None, comment: str) -> None:
    TAGGERS[path.suffix.lower()](path, track, cover, lyrics, comment)
