#!/usr/bin/env bash
# After copying new code over this folder:  ./scripts/update.sh   (backs up first, then rebuilds)
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/backup.sh || true
docker compose up -d --build
docker compose ps
