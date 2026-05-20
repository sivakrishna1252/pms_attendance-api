import json
from dataclasses import dataclass
from urllib import error, request

from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication


@dataclass(frozen=True)
class SharedJWTUser:
    token: dict

    @property
    def id(self):
        return self.token.get("user_id")

    @property
    def employee_id(self):
        return self.id

    @property
    def role(self):
        return self.token.get("role")

    @property
    def is_staff(self):
        return bool(self.token.get("is_staff") or self.token.get("is_superuser"))

    @property
    def is_authenticated(self):
        return True

    def __str__(self):
        return str(self.employee_id)


def _fetch_pms_profile_claims(raw_token):
    base_url = getattr(settings, "PMS_API_BASE_URL", "") or ""
    if not base_url:
        return {}

    token_text = raw_token.decode() if isinstance(raw_token, bytes) else str(raw_token)
    authorization = token_text if token_text.lower().startswith("bearer ") else f"Bearer {token_text}"
    profile_request = request.Request(
        f"{base_url.rstrip('/')}/auth/me",
        headers={"Authorization": authorization, "Accept": "application/json"},
    )
    try:
        with request.urlopen(profile_request, timeout=3) as response:
            body = json.loads(response.read().decode())
    except (error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return {}

    profile = body.get("data") or {}
    claims = {}
    role = profile.get("role")
    if role:
        claims["role"] = role
    return claims


class SharedJWTAuthentication(JWTAuthentication):
    """
    Validates PMS-issued SimpleJWT tokens without looking up a local user.

    The attendance microservice keeps its own database, so it must not depend on
    PMS auth tables. The token's user_id claim is treated as employee_id.
    """

    def get_raw_token(self, header):
        if not header:
            return None
        parts = header.split()
        if len(parts) == 1 and parts[0].startswith(b"eyJ"):
            return parts[0]
        return super().get_raw_token(header)

    def authenticate(self, request):
        header = self.get_header(request)
        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)
        payload = dict(validated_token.payload)
        if not payload.get("role"):
            payload.update(_fetch_pms_profile_claims(raw_token))
        return SharedJWTUser(payload), validated_token

    def get_user(self, validated_token):
        return SharedJWTUser(dict(validated_token.payload))
