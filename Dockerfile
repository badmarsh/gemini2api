FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget gosu && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app/ ./app/
COPY static/ ./static/
# Bake api/ runtime assets (QR configuration and images) into image for self-containment:
# Ensures /app/api exists even if docker run does not mount ./api:/app/api, preventing StaticFiles crash;
# Compose bind mounts can still override these defaults at runtime.
# Note: .dockerignore excludes api by default, release pipelines temporarily un-exclude it before build.
COPY api/ ./api/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Run as non-root: reduce blast radius (security hardening). UID 1000 aligns with common host users
# and refresher's pwuser for consistent ./data ownership. The container starts as root so
# docker-entrypoint.sh can fix bind-mount permissions, then drops privileges to appuser via gosu.
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 5918

# Container healthcheck: enables liveness signals for standalone docker run (compose has its own check).
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD wget -q -O /dev/null http://localhost:5918/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5918"]
