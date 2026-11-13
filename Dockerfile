FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite+aiosqlite:////data/bot.db \
    BACKUP_DIR=/data/backups

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# /data holds the database + backups (mount a volume there: ./data on a VPS, a Railway Volume on Railway).
# The entrypoint fixes /data permissions, then runs the bot as the unprivileged "bot" user.
RUN useradd -m -u 1000 bot && mkdir -p /data && chown -R bot:bot /app /data && chmod +x docker-entrypoint.sh

# healthy while the bot's maintenance loop keeps touching its heartbeat file (see healthcheck.py)
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 CMD ["python", "healthcheck.py"]

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "bot.py"]
