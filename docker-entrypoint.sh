#!/bin/sh
# Entrypoint script (Non-root container permission hardening):
# When starting as root, grant ownership of writable volumes (/app/data, /app/api)
# to appuser, then drop privileges (gosu) to appuser to run the main service.
#
# Why this is necessary: The container process runs as non-root (appuser), but bind mounts
# such as ./data reflect host ownership (often created by prior root containers or arbitrary UIDs).
# Without repairing ownership, non-root processes encounter PermissionError when writing
# cookies, logs, and usage stats. This entrypoint enables seamless upgrades without manual chown.
#
# If the container is explicitly started as non-root (e.g. compose user: directive), chown is
# skipped and the service is executed directly with host-managed permissions.
set -e

if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser /app/data /app/api 2>/dev/null || true
    exec gosu appuser "$@"
fi

exec "$@"
