import uuid

from django.db import models


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.URLField(max_length=500)
    kind = models.CharField(max_length=10)
    title = models.CharField(max_length=300)
    subtitle = models.CharField(max_length=300, blank=True)
    cover_url = models.URLField(max_length=500, blank=True)
    quality = models.CharField(max_length=20)
    created = models.DateTimeField(auto_now_add=True)


class JobItem(models.Model):
    PENDING, WORKING, DONE, ERROR = "pending", "working", "done", "error"
    STATUSES = [(s, s) for s in (PENDING, WORKING, DONE, ERROR)]

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="items")
    index = models.PositiveIntegerField()
    track_id = models.CharField(max_length=40)
    track_json = models.TextField()
    title = models.CharField(max_length=300)
    artist = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    error = models.CharField(max_length=300, blank=True)
    filename = models.CharField(max_length=300, blank=True)
    size = models.BigIntegerField(default=0)
    label = models.CharField(max_length=100, blank=True)
    source = models.CharField(max_length=150, blank=True)
    lyrics = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["index"]
