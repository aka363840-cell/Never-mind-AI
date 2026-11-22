#!/bin/sh
# Safe online backup of the Ella Gaming database (SQLite WAL). Cron example:  15 3 * * * /opt/ella_gaming/deploy/backup.sh
set -eu
DATA="${ELLA_DATA_DIR:-$(dirname "$0")/../data}"
OUT="${1:-$DATA/backups}"; mkdir -p "$OUT"
python3 - "$DATA/ella_gaming.sqlite3" "$OUT/ella_gaming-$(date +%F).sqlite3" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()
PY
ls -1t "$OUT"/ella_gaming-*.sqlite3 | tail -n +15 | xargs -r rm --
