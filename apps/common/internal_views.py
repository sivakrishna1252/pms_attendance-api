from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.authentication.permissions import IsServiceToken
from apps.common.ai_readonly_context import build_attendance_readonly_snapshot


@extend_schema(tags=["Internal APIs"])
class InternalAIReadonlySnapshotAPIView(APIView):
    """
    Service-to-service read-only snapshot for PMS admin AI.
    ORM reads only — no create/update/delete.
    """

    authentication_classes = []
    permission_classes = [IsServiceToken]

    def get(self, request):
        snapshot = build_attendance_readonly_snapshot()
        return Response(
            {
                "success": True,
                "message": "Attendance read-only snapshot fetched.",
                "data": snapshot,
            },
            status=status.HTTP_200_OK,
        )
