#!/usr/bin/env bash
# Safe online backup of the SQLite database (Docker setup). Keeps the newest 14 in ./backups
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="data/bot.db"; [ -f "$SRC" ] || SRC="bot.db"
[ -f "$SRC" ] || { echo "No database found (data/bot.db)."; exit 1; }
mkdir -p backups
OUT="backups/bot_$(date +%Y-%m-%d_%H%M).db"
python3 - "$SRC" "$OUT" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
ls -1t backups/bot_*.db | tail -n +15 | xargs -r rm -f
echo "✅ Backup saved: $OUT   (copy it off the server too)"
