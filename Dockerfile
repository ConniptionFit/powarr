# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Backend + serve built frontend
FROM python:3.12-slim AS final
WORKDIR /app

# postgresql-client-16 (matched to this deployment's Postgres 16 server, via the
# PGDG apt repo — Debian bookworm's own repo only ships v15) for scheduled
# pg_dump backups (v0.26.0). Build tools removed again after install.
RUN apt-get update && apt-get install -y --no-install-recommends wget gnupg ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && wget -qO /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
    && apt-get purge -y --auto-remove wget gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ ./app/
COPY --from=frontend-build /frontend/dist ./static/

ENV POWARR_DATA_DIR=/config
VOLUME ["/config"]
EXPOSE 7979

# Non-root user prepared for SEC-05 / CONTROL-04 — not activated by default
# (host /config volume is typically root-owned; flip to USER powarr after
# `chown -R 7979:7979` on the volume).
RUN useradd --create-home --uid 7979 --shell /usr/sbin/nologin powarr \
    && mkdir -p /config

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7979/api/v1/system/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7979"]
