from django.urls import path

from apps.common.internal_views import InternalAIReadonlySnapshotAPIView

urlpatterns = [
    path(
        "ai-readonly-snapshot/",
        InternalAIReadonlySnapshotAPIView.as_view(),
        name="internal-ai-readonly-snapshot",
    ),
]
