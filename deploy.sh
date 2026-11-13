#!/usr/bin/env bash
# One-command VPS deploy with Docker (Ubuntu 22.04/24.04, Debian 12). Run:  sudo ./deploy.sh
# Never overwrites or prints your .env.
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" -eq 0 ] || { echo "Please run with sudo:  sudo ./deploy.sh"; exit 1; }
[ -f .env ] || { echo "❌ .env not found. Create it first:  cp .env.example .env  (then edit)  or  python3 setup_env.py"; exit 1; }
for k in BOT_TOKEN OWNER_ID; do
  grep -E "^${k}=.+" .env >/dev/null || { echo "❌ ${k} is empty in .env"; exit 1; }
done

if ! command -v docker >/dev/null 2>&1; then
  echo "▶ Installing Docker..."
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# Firewall: SSH only (the bot uses outgoing long-polling, it needs no open ports).
if command -v ufw >/dev/null 2>&1; then
  SSH_PORT="$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')"
  ufw allow "${SSH_PORT:-22}/tcp" >/dev/null
  ufw --force enable >/dev/null
fi

mkdir -p data
# Moving an existing game over? A bot.db next to this script is copied into ./data once.
if [ -f bot.db ] && [ ! -f data/bot.db ]; then
  echo "▶ Found an existing bot.db - copying it into ./data (your game data is kept)."
  cp bot.db* data/ 2>/dev/null || cp bot.db data/
fi
chown -R 1000:1000 data       # the container runs as uid 1000

echo "▶ Building and starting the bot..."
docker compose up -d --build
sleep 5
docker compose ps
echo
echo "✅ Done. Live logs:  docker compose logs -f bot"
echo "   Then send /start to your bot on Telegram."
