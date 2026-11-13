#!/bin/sh
# Railway/Docker volumes are usually root-owned. Fix /data once, then drop to the unprivileged "bot" user.
set -e
mkdir -p "${DATA_DIR:-/data}" 2>/dev/null || true
if [ "$(id -u)" = "0" ]; then
  chown -R bot:bot "${DATA_DIR:-/data}" 2>/dev/null || true
  exec setpriv --reuid=1000 --regid=1000 --init-groups "$@"
fi
exec "$@"
