"""Registered audio sources, in priority order. Add new source plugins here."""
from __future__ import annotations

from .base import AudioSource, RawAudio, SourceNotFound
from .youtube import YouTubeMusicSource

ALL_SOURCES: list[AudioSource] = [
    # Put sources that deliver real lossless / 320 kbps audio before YouTube.
    YouTubeMusicSource(),
]


def enabled_sources() -> list[AudioSource]:
    return [s for s in ALL_SOURCES if s.is_enabled()]


__all__ = ["AudioSource", "RawAudio", "SourceNotFound", "ALL_SOURCES", "enabled_sources"]
