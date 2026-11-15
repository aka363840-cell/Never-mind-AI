"""Every Academy Life router, in registration order. build_dispatcher (bot.py) includes routers() BEFORE the free-form AI chat
router. A new part adds its handlers/academy_*.py module name to MODULES (each module exposes `router`)."""
from __future__ import annotations

import importlib

from aiogram import Router

MODULES: tuple[str, ...] = (
    "handlers.academy_staff",     # hidden staff commands first (non-staff never match them)
    "handlers.academy_core",     # Part 1: /enroll /academy /schedule
    "handlers.academy_life",     # Part 2: /study /attend /hangout /rest /club /parttime
    "handlers.academy_exams",    # Part 3: /exam /grades /classrank
    "handlers.academy_social",   # Part 4: /rival /rivalduel /crush /crushoff /campuscouple /campusdate /clubs /buddy
    "handlers.academy_adventures",   # Part 5: /adventure
    "handlers.academy_remind",   # Part 6: /academyremind /reportcard /halloffame + the Enroll button of group announcements
    "handlers.academy_help",     # Part 7: /academyhelp (inline pages, callbacks ach:)
)


def routers() -> list[Router]:
    return [importlib.import_module(m).router for m in MODULES]
