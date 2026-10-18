"""Download qualities. A quality is only offered when an enabled source can deliver it for real."""
from __future__ import annotations

from dataclasses import dataclass

from .sources import enabled_sources


@dataclass(frozen=True)
class Quality:
    key: str
    group: str  # what the user picks first: "mp3_128" | "best" | "mp3_320" | "flac"
    label: str
    short: str
    tiers: tuple[str, ...]  # source tiers that can produce it, best first


QUALITIES: dict[str, Quality] = {
    q.key: q
    for q in (
        Quality("mp3_128", "mp3_128", "MP3 · 128kbps", "128kbps", ("lossless", "mp3_320", "lossy")),
        Quality("best_mp3", "best", "بهترین کیفیت · MP3", "Best · MP3", ("lossless", "mp3_320", "lossy")),
        Quality("best_orig", "best", "بهترین کیفیت · فرمت اصلی", "Best · Original", ("lossless", "mp3_320", "lossy")),
        Quality("mp3_320", "mp3_320", "MP3 · 320kbps", "320kbps", ("lossless", "mp3_320")),
        Quality("flac", "flac", "FLAC · Lossless", "FLAC", ("lossless",)),
    )
}

GROUPS = {
    "mp3_128": {"label": "MP3 · 128kbps", "emoji": "🎧", "hint": "حجم کم، مناسب گوشی"},
    "best": {"label": "بهترین کیفیت موجود", "emoji": "💎", "hint": "بالاترین کیفیت واقعی منبع"},
    "mp3_320": {"label": "MP3 · 320kbps", "emoji": "🔥", "hint": "۳۲۰ واقعی از منبع باکیفیت"},
    "flac": {"label": "FLAC · Lossless", "emoji": "💿", "hint": "بدون افت کیفیت"},
}


def _available_tiers() -> set[str]:
    tiers: set[str] = set()
    for source in enabled_sources():
        tiers |= source.tiers
    return tiers


def is_available(key: str) -> bool:
    q = QUALITIES.get(key)
    return bool(q) and bool(set(q.tiers) & _available_tiers())


def available_groups() -> list[str]:
    return [g for g in GROUPS if any(q.group == g and is_available(q.key) for q in QUALITIES.values())]


def describe() -> list[dict]:
    """Every quality group with its availability, for the website."""
    return [
        {
            "group": g,
            **GROUPS[g],
            "available": g in available_groups(),
            "qualities": [{"key": q.key, "label": q.label} for q in QUALITIES.values() if q.group == g],
        }
        for g in GROUPS
    ]
