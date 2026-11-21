# Project rules (owner's standing instructions)

- **Nobody may miss a feature.** Every new command or feature must be reachable from BOTH `/help` and `/start`:
  - `/help`: add it to `DESC_LIST` (+ `ALIASES`) and a `CATEGORIES` entry in `utils/helpcatalog.py`. `tests/test_help_catalog.py` fails if a public command is missing.
  - `/start`: it must be reachable by buttons - a page/card in the matching hub (Games hub in `handlers/arcade.py`, Group Tools, Fun & Social, ...) and the page's "📖 All … commands" button (`PAGE_HELP` in `handlers/start.py`).
- All games live together under **Games** (help category "🕹 Games" and the 🕹 Games hub); only one game can run per chat (`services/wordgames.running/busy`).
- New game commands stay OUT of Telegram's "/" menu (`MENU` in `utils/helpcatalog.py`) unless the owner asks.
- Menus/pages edit the same message in place (no separate new message).
- Keep replies short; don't change things that were not asked; say plainly when something is not tested live.
