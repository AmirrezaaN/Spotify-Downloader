"""SQLite cache of finished files, shared by the bot and the website.

Each (track, quality) is downloaded once. The bot also stores Telegram's file_id, so a track
that was sent once is re-sent instantly even after the local file was cleaned up.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings


@dataclass
class CacheEntry:
    key: str
    track_id: str
    quality: str
    path: Path | None
    filename: str
    size: int
    info: dict = field(default_factory=dict)
    tg_file_id: str | None = None

    @property
    def has_file(self) -> bool:
        return bool(self.path) and self.path.exists()


class Cache:
    def __init__(self, db_path: Path) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS files (
                key TEXT PRIMARY KEY,
                track_id TEXT NOT NULL,
                quality TEXT NOT NULL,
                path TEXT,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL DEFAULT 0,
                info TEXT NOT NULL DEFAULT '{}',
                tg_file_id TEXT,
                created_at REAL NOT NULL,
                used_at REAL NOT NULL
            )"""
        )
        self._db.commit()

    @staticmethod
    def make_key(track_id: str, quality: str) -> str:
        return f"{track_id}:{quality}"

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            row = self._db.execute(
                "SELECT key, track_id, quality, path, filename, size, info, tg_file_id FROM files WHERE key = ?",
                (key,),
            ).fetchone()
            if row:
                self._db.execute("UPDATE files SET used_at = ? WHERE key = ?", (time.time(), key))
                self._db.commit()
        if not row:
            return None
        return CacheEntry(
            key=row[0], track_id=row[1], quality=row[2], path=Path(row[3]) if row[3] else None,
            filename=row[4], size=row[5], info=json.loads(row[6] or "{}"), tg_file_id=row[7],
        )

    def put(self, entry: CacheEntry) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT INTO files (key, track_id, quality, path, filename, size, info, tg_file_id, created_at, used_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET path=excluded.path, filename=excluded.filename,
                       size=excluded.size, info=excluded.info, created_at=excluded.created_at, used_at=excluded.used_at""",
                (entry.key, entry.track_id, entry.quality, str(entry.path) if entry.path else None, entry.filename,
                 entry.size, json.dumps(entry.info, ensure_ascii=False), entry.tg_file_id, now, now),
            )
            self._db.commit()

    def set_tg_file_id(self, key: str, file_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE files SET tg_file_id = ? WHERE key = ?", (file_id, key))
            self._db.commit()

    def cleanup(self, max_age_days: int) -> int:
        """Delete local files not used for max_age_days. Rows (and Telegram file_ids) are kept."""
        if max_age_days <= 0:
            return 0
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            rows = self._db.execute(
                "SELECT key, path FROM files WHERE path IS NOT NULL AND used_at < ?", (cutoff,)
            ).fetchall()
            for key, path in rows:
                Path(path).unlink(missing_ok=True)
                self._db.execute("UPDATE files SET path = NULL WHERE key = ?", (key,))
            self._db.commit()
        return len(rows)


cache = Cache(settings.cache_db)
