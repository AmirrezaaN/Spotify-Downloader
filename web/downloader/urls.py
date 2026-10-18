from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/resolve", views.api_resolve, name="api_resolve"),
    path("api/jobs", views.api_create_job, name="api_create_job"),
    path("api/jobs/<uuid:job_id>", views.api_job, name="api_job"),
    path("download/<int:item_id>", views.download_file, name="download"),
]
