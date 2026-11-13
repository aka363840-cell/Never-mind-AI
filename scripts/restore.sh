#!/usr/bin/env bash
# Restore a backup:  sudo ./scripts/restore.sh backups/bot_YYYY-MM-DD_HHMM.db
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-}"; [ -f "$FILE" ] || { echo "Usage: $0 <backup .db file>"; exit 1; }
echo "▶ Stopping the bot..."; docker compose stop bot
[ -f data/bot.db ] && cp data/bot.db "data/bot.db.before-restore-$(date +%s)"
rm -f data/bot.db-wal data/bot.db-shm
cp "$FILE" data/bot.db
chown 1000:1000 data/bot.db
docker compose up -d bot
echo "✅ Restored from $FILE"
