# HRMS Attendance Service — Production

## Endpoints

| Access | Base URL |
|--------|----------|
| Public API (recommended) | `https://nexus-hrms.aspune.cloud` or `http://nexus-hrms.aspune.cloud` |
| Host port (Docker / direct) | `http://127.0.0.1:6015` (maps to Gunicorn `8000` in container) |
| API prefix | `/api/attendance/`, `/api/leaves/`, `/api/admin/` |
| Docs | `/api/docs/` |

## Environment

Set in server `.env` or Jenkins credential `hrms_attendance_env`:

```env
ALLOWED_HOSTS=127.0.0.1,localhost,nexus-hrms.aspune.cloud
APP_PORT=6015
DEBUG=False
PMS_API_BASE_URL=http://nexus-pms.aspune.cloud/api/v1
JWT_SECRET=<same as PMS Django SECRET_KEY>
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:6012,https://nexus-hrms.aspune.cloud,https://nexus-pms.aspune.cloud
```

Adjust `CORS_ALLOWED_ORIGINS` to every **browser origin** that will call this API (exact scheme/host/port). If your DNS hostname differs from `nexus-hrms.aspune.cloud`, add it to `ALLOWED_HOSTS` and CORS.

## Jenkins credential (required)

The pipeline loads production `.env` from a Jenkins **Secret file** credential. Create it once on the Jenkins server:

1. **Manage Jenkins** → **Credentials** → **System** → **Global credentials** → **Add Credentials**
2. Kind: **Secret file**
3. **ID:** `hrms_attendance_env` (must match `ENV_ID` in `Jenkinsfile`)
4. **File:** upload a production `.env` (copy from `.env.example`, set `DEBUG=False`, DB, `JWT_SECRET`, CORS, SMTP, etc.)
5. Use `DB_USER` (not `DB_USERNAME`). DB name on server is `attendace_pms` if that database already exists.

PMS uses the same pattern with credential ID `pms_backend_env`.

## Deploy

**Docker Compose:**

```bash
cd attendance_service
docker compose -f docker-compose.prod.yml up -d --build
```

**Jenkins:** run pipeline from `attendance_service/Jenkinsfile` (container `hrms-attendance-prod`, host port **6015**).

## Nginx

Point DNS `nexus-hrms.aspune.cloud` at your server, then proxy to `127.0.0.1:6015` (see `README.md` for sample config). Prefer HTTPS on nginx and include `https://…` entries in `CORS_ALLOWED_ORIGINS` for browser clients.

Verify:

```bash
curl -s http://127.0.0.1:6015/
curl -s http://nexus-hrms.aspune.cloud/ -H "Host: nexus-hrms.aspune.cloud"
```
