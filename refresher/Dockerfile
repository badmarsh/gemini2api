FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# Unbuffered stdout/stderr: without a TTY Python block-buffers prints, so `docker logs` showed
# nothing for hours and a refresh cycle could not be followed live.
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-zenhei curl procps \
    && rm -rf /var/lib/apt/lists/*

# Pin requests version to ensure reproducible builds.
RUN pip install --no-cache-dir playwright==1.44.0 requests==2.32.3

COPY refresher.py .

# Run as non-root: use built-in pwuser from Playwright base image to drop privileges.
# /app is owned by pwuser; mounted /app/data write permissions are handled at runtime by compose.
RUN chown -R pwuser:pwuser /app
USER pwuser

# Process-level healthcheck: mark unhealthy if refresher hangs or exits (e.g. Chromium crash),
# enabling orchestration auto-restart.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f refresher.py >/dev/null || exit 1

CMD ["python", "refresher.py"]
