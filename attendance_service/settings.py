from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="django-insecure-attendance-dev-key")
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "apps.authentication",
    "apps.attendance",
    "apps.leaves",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "attendance_service.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "attendance_service.wsgi.application"

_default_db = {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": config("DB_NAME", default="attendance_pms"),
    "USER": config("DB_USER", default="") or config("DB_USERNAME", default="postgres"),
    "PASSWORD": config("DB_PASSWORD", default="siva"),
    "HOST": config("DB_HOST", default="127.0.0.1"),
    "PORT": config("DB_PORT", default="5432"),
}

DATABASES = {"default": _default_db}

# Optional read/write bridge to PMS DB for in-app notifications when internal HTTP API is unavailable.
_pms_db_name = config("PMS_DB_NAME", default="pms").strip()
if _pms_db_name:
    DATABASES["pms"] = {
        **_default_db,
        "NAME": _pms_db_name,
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.authentication.authentication.SharedJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Attendance Management Microservice API",
    "DESCRIPTION": (
        "Standalone attendance, leave, and report APIs. Login stays in the PMS backend; "
        "send the PMS JWT access token as Authorization: Bearer <token>."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "SIGNING_KEY": config("JWT_SECRET", default=SECRET_KEY),
}

CORS_ALLOWED_ORIGINS = config(
    
    "CORS_ALLOWED_ORIGINS",
    default="http://localhost:3000",
    cast=Csv(),
)
CORS_ALLOW_CREDENTIALS = True
CORS_EXPOSE_HEADERS = [
    "Content-Disposition",
    "Content-Type",
    "Content-Length",
]

ADMIN_EMPLOYEE_IDS = {
    int(value)
    for value in config("ADMIN_EMPLOYEE_IDS", default="", cast=Csv())
    if str(value).strip().isdigit()
}

# Optional: resolve role for legacy PMS tokens that only contain user_id.
# Production: set PMS_API_BASE_URL in .env (see .env.example).
PMS_API_BASE_URL = config("PMS_API_BASE_URL", default="http://127.0.0.1:8000/api/v1").strip().rstrip("/")
PMS_SERVICE_TOKEN = config("PMS_SERVICE_TOKEN", default="")
PMS_FRONTEND_URL = config(
    "PMS_FRONTEND_URL",
    default="https://nexus-pms.aspune.cloud",
).strip().rstrip("/")

EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
# Same env names as PMS (`SMTP_USER` / `SMTP_PASS`) or Django-style `EMAIL_HOST_*`.
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="") or config("SMTP_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="") or config("SMTP_PASS", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)

LEAVE_REQUEST_TO_EMAIL = config(
    "LEAVE_REQUEST_TO_EMAIL",
    default="harsh.singh@apparatus.solutions",
).strip()
LEAVE_REQUEST_CC_EMAILS = [
    email.strip()
    for email in config(
        "LEAVE_REQUEST_CC_EMAILS",
        default="Vivek@apparatus.solutions,Rishabh@apparatus.solutions",
        cast=Csv(),
    )
    if email.strip()
]
