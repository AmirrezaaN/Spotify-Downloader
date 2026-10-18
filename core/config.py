"""Settings shared by the bot and the website, read from the .env file at the project root."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = _str(name).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _path(name: str, default: Path | None) -> Path | None:
    value = _str(name)
    if not value:
        return default
    p = Path(value)
    return p if p.is_absolute() else ROOT_DIR / p


def _find_deno() -> str | None:
    # Prefer the deno binary installed in the virtualenv by `pip install deno`.
    local = Path(sys.executable).parent / ("deno.exe" if os.name == "nt" else "deno")
    if local.exists():
        return str(local)
    return shutil.which("deno")


@dataclass(frozen=True)
class Settings:
    app_name: str
    data_dir: Path
    proxy: str | None
    max_concurrent: int
    playlist_limit: int
    lyrics_enabled: bool
    lyrics_style: str
    keep_files_days: int
    yt_cookies: Path | None
    deno_path: str | None
    ffmpeg_path: str

    telegram_token: str
    telegram_api_url: str | None
    telegram_upload_limit_mb: int

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def cache_db(self) -> Path:
        return self.data_dir / "cache.sqlite3"

    @property
    def proxies(self) -> dict[str, str] | None:
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.library_dir, self.tmp_dir, self.covers_dir):
            d.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    api_url = _str("TELEGRAM_API_URL") or None
    return Settings(
        app_name=_str("APP_NAME", "SpotiLoad"),
        data_dir=_path("DATA_DIR", ROOT_DIR / "data"),
        proxy=_str("PROXY_URL") or None,
        max_concurrent=max(1, _int("MAX_CONCURRENT_DOWNLOADS", 3)),
        playlist_limit=max(1, _int("PLAYLIST_LIMIT", 100)),
        lyrics_enabled=_bool("LYRICS_ENABLED", True),
        lyrics_style=_str("LYRICS_STYLE", "synced").lower(),
        keep_files_days=_int("KEEP_FILES_DAYS", 7),
        yt_cookies=_path("YT_COOKIES_FILE", None),
        deno_path=_find_deno(),
        ffmpeg_path=_str("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg",
        telegram_token=_str("TELEGRAM_BOT_TOKEN"),
        telegram_api_url=api_url,
        # The public Bot API caps uploads at 50 MB; a local Bot API server allows 2000 MB.
        telegram_upload_limit_mb=_int("TELEGRAM_UPLOAD_LIMIT_MB", 2000 if api_url else 50),
    )


settings = load_settings()
settings.ensure_dirs()
