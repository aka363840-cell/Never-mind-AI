"""Start file for panels whose default startup file is main.py (bot-hosting.net etc.). Same as: python bot.py"""
import asyncio

import bot

if __name__ == "__main__":
    bot._tune_memory()
    asyncio.run(bot.main())
