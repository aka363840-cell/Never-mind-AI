"""Docker HEALTHCHECK: healthy while the bot's maintenance loop has written a heartbeat in the last 3 minutes."""
import os, sys, time
f = os.getenv("HEARTBEAT_FILE", "/tmp/bot.heartbeat")
try:
    sys.exit(0 if time.time() - os.path.getmtime(f) < 180 else 1)
except OSError:
    sys.exit(1)
