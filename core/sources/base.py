"""Audio source plugin interface.

A source finds a Spotify track somewhere else and downloads its audio *as it is* (no re-encoding).
It declares which tiers it can really deliver; the pipeline only offers a quality when some
enabled source can deliver it honestly:

    "lossy"    any lossy stream (e.g. YouTube Music Opus / AAC)
    "mp3_320"  a genuine 320 kbps MP3 stream
    "lossless" FLAC / ALAC (also lets the pipeline make a genuine 320 kbps MP3)

To add a source: subclass AudioSource, implement `fetch`, and register it in `sources/__init__.py`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..models import Track


class SourceNotFound(Exception):
    """The source has no good match for this track."""


@dataclass
class RawAudio:
    path: Path
    codec: str  # "opus" | "aac" | "mp3" | "flac" | "vorbis"
    bitrate_kbps: float | None
    sample_rate: int | None
    lossless: bool
    source_title: str  # e.g. "YouTube Music"
    source_url: str | None = None

    @property
    def description(self) -> str:
        """Human readable, e.g. "YouTube Music · Opus 129kbps"."""
        codec = {"aac": "AAC", "opus": "Opus", "mp3": "MP3", "flac": "FLAC", "vorbis": "Vorbis"}.get(self.codec, self.codec)
        if self.lossless:
            detail = f"{codec} {self.sample_rate // 1000 if self.sample_rate else ''}kHz".strip()
        elif self.bitrate_kbps:
            detail = f"{codec} {round(self.bitrate_kbps)}kbps"
        else:
            detail = codec
        return f"{self.source_title} · {detail}"


class AudioSource(ABC):
    name: str = ""
    title: str = ""
    tiers: frozenset[str] = frozenset()

    def is_enabled(self) -> bool:
        return True

    @abstractmethod
    def fetch(self, track: Track, tier: str, workdir: Path) -> RawAudio:
        """Download the best stream of `tier` for `track` into workdir or raise SourceNotFound."""
