"""Runs downloads in background threads inside the web process (no Celery/Redis needed)."""
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from core.cache import cache
from core.config import settings
from core.models import Track
from core.pipeline import DownloadError, download

from .models import JobItem

log = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=settings.max_concurrent, thread_name_prefix="download")
_last_cleanup = 0.0
_cleanup_lock = threading.Lock()


def submit(job) -> None:
    for item_id in job.items.values_list("id", flat=True):
        _executor.submit(_run_item, item_id, job.quality)
    _maybe_cleanup()


def _run_item(item_id: int, quality: str) -> None:
    close_old_connections()
    try:
        item = JobItem.objects.get(pk=item_id)
        item.status = JobItem.WORKING
        item.save(update_fields=["status"])
        try:
            result = download(Track.from_dict(json.loads(item.track_json)), quality)
        except DownloadError as exc:
            item.status, item.error = JobItem.ERROR, str(exc)[:300]
        except Exception:
            log.exception("download failed for %s", item.track_id)
            item.status, item.error = JobItem.ERROR, "خطای غیرمنتظره موقع دانلود"
        else:
            item.status = JobItem.DONE
            item.filename = result.filename
            item.size = result.entry.size
            item.label = result.info.get("label", "")
            item.source = result.info.get("source", "")
            item.lyrics = result.info.get("lyrics") or ""
        item.save()
    finally:
        close_old_connections()


def _maybe_cleanup() -> None:
    global _last_cleanup
    with _cleanup_lock:
        if time.time() - _last_cleanup < 6 * 3600:
            return
        _last_cleanup = time.time()
    _executor.submit(cache.cleanup, settings.keep_files_days)
