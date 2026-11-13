"""Interactive .env creator: python setup_env.py
Your secrets are typed here and written only to the local .env file (never printed, never committed)."""
from __future__ import annotations

import getpass
import os
import re
import secrets
import sys

TEMPLATE = ".env.example"


def ask(label: str, default: str = "", secret: bool = False) -> str:
    prompt = f"{label}" + (f" [{default}]" if default and not secret else "") + ": "
    val = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    return val or default


def main() -> None:
    if os.path.exists(".env") and ask("`.env` already exists. Overwrite? (yes/no)", "no").lower() != "yes":
        sys.exit("Aborted, nothing changed.")
    print("Get a NEW bot token from @BotFather and a NEW Gemini key from https://aistudio.google.com/apikey\n")
    token = ask("BOT_TOKEN", secret=True)
    owner = ask("OWNER_ID (your numeric Telegram id)", "8445198778")
    gem = ask("GEMINI_API_KEY", secret=True)
    use_docker = ask("Will you run with Docker Compose? (yes/no)", "yes").lower() == "yes"
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        sys.exit("That doesn't look like a Telegram bot token (expected 123456789:AA...).")
    if not owner.isdigit():
        sys.exit("OWNER_ID must be numeric.")
    if not gem:
        print("! No Gemini key: the AI will be disabled, games still work.")

    text = open(TEMPLATE, encoding="utf-8").read()
    text = re.sub(r"^BOT_TOKEN=.*$", f"BOT_TOKEN={token}", text, flags=re.M)
    text = re.sub(r"^OWNER_ID=.*$", f"OWNER_ID={owner}", text, flags=re.M)
    text = re.sub(r"^GEMINI_API_KEY=.*$", f"GEMINI_API_KEY={gem}", text, flags=re.M)
    if use_docker:
        pw = secrets.token_urlsafe(16)
        text += f"\nPOSTGRES_PASSWORD={pw}\n"
    with open(".env", "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.chmod(".env", 0o600)
    except OSError:
        pass
    print("\n.env written.")
    print("Next:", "docker compose up -d --build" if use_docker else "alembic upgrade head && python bot.py")
    print("Then run `python check_ai.py` any time to test Gemini, or send /aitest to the bot (owner only).")


if __name__ == "__main__":
    main()
