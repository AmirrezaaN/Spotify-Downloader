import json
import os

from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from core import spotify
from core.config import settings
from core.models import Track
from core.pipeline import DownloadError, download, lookup
from core.qualities import QUALITIES, describe, is_available

from . import worker
from .models import Job, JobItem


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}


@ensure_csrf_cookie
@require_GET
def index(request):
    bot = os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    return render(request, "downloader/index.html", {
        "app_name": settings.app_name,
        "bot_username": bot,
        "qualities": describe(),
    })


@require_POST
def api_resolve(request):
    url = (_body(request).get("url") or "").strip()
    if not url:
        return _error("لینک رو وارد کن.")
    try:
        collection = spotify.resolve(url)
    except spotify.SpotifyError as exc:
        return _error(str(exc))
    return JsonResponse({"collection": collection.to_dict(), "qualities": describe()})


@require_POST
def api_create_job(request):
    data = _body(request)
    quality = data.get("quality", "")
    if quality not in QUALITIES or not is_available(quality):
        return _error("این کیفیت فعلاً در دسترس نیست.")
    try:
        collection = spotify.resolve(data.get("url") or "")
    except spotify.SpotifyError as exc:
        return _error(str(exc))

    job = Job.objects.create(
        url=collection.url, kind=collection.kind, title=collection.title[:300],
        subtitle=collection.subtitle[:300], cover_url=collection.cover_url or "", quality=quality,
    )
    JobItem.objects.bulk_create([
        JobItem(job=job, index=i, track_id=t.id, track_json=json.dumps(t.to_dict(), ensure_ascii=False),
                title=t.title[:300], artist=t.artist[:300])
        for i, t in enumerate(collection.tracks, 1)
    ])
    worker.submit(job)
    return JsonResponse({"id": str(job.id)})


@require_GET
def api_job(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    items = []
    for item in job.items.all():
        items.append({
            "id": item.id,
            "index": item.index,
            "track_id": item.track_id,
            "title": item.title,
            "artist": item.artist,
            "status": item.status,
            "error": item.error,
            "filename": item.filename,
            "size": item.size,
            "label": item.label,
            "source": item.source,
            "lyrics": item.lyrics,
            "download_url": reverse("download", args=[item.id]) if item.status == JobItem.DONE else None,
        })
    counts = {s: sum(1 for i in items if i["status"] == s) for s, _ in JobItem.STATUSES}
    return JsonResponse({
        "id": str(job.id),
        "quality": job.quality,
        "quality_label": QUALITIES[job.quality].label,
        "total": len(items),
        "counts": counts,
        "finished": counts["pending"] + counts["working"] == 0,
        "items": items,
    })


@require_GET
def download_file(request, item_id):
    item = get_object_or_404(JobItem, pk=item_id, status=JobItem.DONE)
    entry = lookup(item.track_id, item.job.quality)
    if not entry or not entry.has_file:
        # The file was removed by the periodic cleanup: build it again.
        try:
            entry = download(Track.from_dict(json.loads(item.track_json)), item.job.quality).entry
        except DownloadError as exc:
            raise Http404(str(exc))
    return FileResponse(open(entry.path, "rb"), as_attachment=True, filename=entry.filename)
