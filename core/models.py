from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Track:
    id: str
    title: str
    artists: list[str]
    duration_ms: int
    album: str | None = None
    album_id: str | None = None
    album_artist: str | None = None
    release_date: str | None = None  # "YYYY-MM-DD" or "YYYY"
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int | None = None
    explicit: bool = False
    cover_url: str | None = None
    # Playlist entries only carry title/artists/duration until enriched with the track page.
    partial: bool = False

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/track/{self.id}"

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    @property
    def main_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def year(self) -> str | None:
        return self.release_date[:4] if self.release_date else None

    @property
    def duration_s(self) -> int:
        return round(self.duration_ms / 1000)

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})

    def to_dict(self) -> dict:
        data = asdict(self)
        data["url"] = self.url
        data["artist"] = self.artist
        data["duration_s"] = self.duration_s
        return data


@dataclass
class Collection:
    """What a Spotify link points to: one track, an album or a playlist."""

    kind: str  # "track" | "album" | "playlist"
    id: str
    title: str
    subtitle: str
    cover_url: str | None
    tracks: list[Track] = field(default_factory=list)
    total: int = 0
    color: str | None = None  # dominant cover colour as "#rrggbb"

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/{self.kind}/{self.id}"

    @property
    def truncated(self) -> bool:
        return self.total > len(self.tracks)

    @property
    def duration_s(self) -> int:
        return sum(t.duration_s for t in self.tracks)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "subtitle": self.subtitle,
            "cover_url": self.cover_url,
            "color": self.color,
            "total": self.total,
            "truncated": self.truncated,
            "duration_s": self.duration_s,
            "tracks": [t.to_dict() for t in self.tracks],
        }


def format_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
