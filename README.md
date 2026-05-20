# Attendance Management Microservice

Standalone Django + DRF backend for attendance, leave management, and reports.

## What Was Added

- New folder: `attendance_service`
- New PostgreSQL database target: `attendance_pms`
- Independent Django apps:
  - `apps.attendance` for check-in, check-out, today's attendance, and history
  - `apps.leaves` for leave apply/history and admin approval/rejection
  - `apps.reports` for admin attendance and leave reports
  - `apps.authentication` for validating PMS JWT tokens

This service does not modify the live PMS backend or PMS database.

## Shared JWT Microservice Setup

The PMS backend still owns login. The attendance backend only validates the PMS access token.

Frontend flow:

1. User logs in through PMS backend.
2. PMS backend returns `access` token.
3. Frontend sends the same token to attendance APIs:

```http
Authorization: Bearer <pms_access_token>
```

The attendance service reads the token `user_id` claim as `employee_id`.

Important: `JWT_SECRET` in this service must match the key used by PMS to sign SimpleJWT access tokens. In the current PMS settings, SimpleJWT uses Django `SECRET_KEY` unless a separate signing key is configured.

## Local Database

The `.env` file is configured for the PostgreSQL database visible in pgAdmin:

```env
DB_NAME=attendace_pms
DB_USER=postgres
DB_PASSWORD=siva
DB_HOST=127.0.0.1
DB_PORT=5432
```

## Admin Access

The current PMS access token only contains `user_id`; role data is returned in the login response body, not inside the token. Because this microservice must not query PMS database tables, admin APIs support either:

- future PMS tokens containing `role=ADMIN`, `is_staff=true`, or `is_superuser=true`
- local `.env` fallback:

```env
ADMIN_EMPLOYEE_IDS=1,2
```

Set your admin PMS user id here during local testing.

## Endpoints

Employee attendance:

- `POST /api/attendance/check-in/`
- `POST /api/attendance/check-out/`
- `GET /api/attendance/history/`
- `GET /api/attendance/today/`

Employee leaves:

- `POST /api/leaves/apply/`
- `GET /api/leaves/history/`

Admin:

- `GET /api/admin/leaves/pending/`
- `POST /api/admin/leaves/{id}/approve/`
- `POST /api/admin/leaves/{id}/reject/`
- `GET /api/admin/reports/`

Swagger and docs:

- `GET /api/docs/`
- `GET /api/redoc/`
- `GET /api/schema/`
- See `API_TESTING_GUIDE.md` for request bodies and sample responses.

Health:

- `GET /api/health/`

## Run Locally

```bash
cd attendance_service
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 9000
```

PMS backend can remain on `localhost:8000`, frontend on `localhost:3000`, and attendance backend on `localhost:9000`.

## Production deployment (server)

| Setting | Value |
|--------|--------|
| Public hostname | `nexus-hrms.aspune.cloud` (nginx → `127.0.0.1:6015`) |
| Host port (Docker / Jenkins) | `6015` (maps to Gunicorn `8000` in the container) |
| PMS API (JWT / role resolution) | `http://nexus-pms.aspune.cloud/api/v1` (set `PMS_API_BASE_URL` in `.env`) |

**Local / bypass nginx:** `http://127.0.0.1:6015/`  
**Public URL:** `https://nexus-hrms.aspune.cloud/` (or `http://…` if TLS not yet enabled)  
**Swagger:** `/api/docs/`

If your DNS uses a different hostname than `nexus-hrms.aspune.cloud`, add it to `ALLOWED_HOSTS` and to `CORS_ALLOWED_ORIGINS`.

Copy `.env.example` to `.env` on the server (or Jenkins credential `hrms_attendance_env`). Required values:

```env
ALLOWED_HOSTS=127.0.0.1,localhost,nexus-hrms.aspune.cloud
APP_PORT=6015
PMS_API_BASE_URL=http://nexus-pms.aspune.cloud/api/v1
```

### Docker on the server

```bash
cd attendance_service
cp .env.example .env   # edit secrets / DB / JWT_SECRET
docker compose -f docker-compose.prod.yml up -d --build
curl http://127.0.0.1:6015/
```

### Jenkins

Pipeline: `attendance_service/Jenkinsfile` — builds `hrms-attendance-backend`, runs container `hrms-attendance-prod` on port **6015**.

### Nginx (on Ubuntu server)

Add a site that proxies the HRMS domain to port 6015:

```nginx
server {
    listen 80;
    server_name nexus-hrms.aspune.cloud;

    location / {
        proxy_pass http://127.0.0.1:6015;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Then `sudo nginx -t && sudo systemctl reload nginx`.

Use `listen 443 ssl` and real certificates when exposing HTTPS; add matching `https://…` origins to `CORS_ALLOWED_ORIGINS`.
