#!/usr/bin/env bash
# No-Docker VPS install (SQLite, runs as a systemd service).  Run from anywhere:  sudo ./deploy/install-systemd.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo."; exit 1; }
[ -f .env ] || { echo "❌ .env not found in $DIR"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "❌ Python 3.11+ is required (Ubuntu 24.04 / Debian 12 have it)."; exit 1; }

apt-get update -y && apt-get install -y python3-venv python3-pip
id botuser >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin botuser
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
chown -R botuser:botuser "$DIR"
chmod 600 .env

sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|botuser|g" deploy/bot.service > /etc/systemd/system/telegram-bot.service
systemctl daemon-reload
systemctl enable --now telegram-bot
sleep 3
systemctl --no-pager status telegram-bot | head -n 12
echo
echo "✅ Installed. Live logs:  journalctl -u telegram-bot -f      Restart:  sudo systemctl restart telegram-bot"
