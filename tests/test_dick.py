"""🍆 /growdick, /pvp and /dicktop: real Dispatcher + middlewares + handlers, fake Telegram transport, GROUP chats.
Run: pytest tests/test_dick.py"""
import asyncio
import os
import random
import tempfile
import time

DB = os.path.join(tempfile.gettempdir(), "dick_test.db")
URL = f"sqlite+aiosqlite:///{DB}"
GROUP, GROUP2 = -1001, -1002


def run(coro):
    return asyncio.run(coro)


class GroupHarness:
    def __init__(self):
        from aiogram import Bot
        from aiogram.client.session.base import BaseSession
        outer = self

        class FakeSession(BaseSession):
            async def close(self): pass
            async def stream_content(self, *a, **k):
                yield b""

            async def make_request(self, bot, method, timeout=None):
                name = type(method).__name__
                outer.calls.append((name, method))
                if name == "SendMessage":
                    outer.mid += 1
                    return {"message_id": outer.mid, "date": int(time.time()), "chat": {"id": method.chat_id, "type": "supergroup", "title": "G"}, "text": method.text}
                if name == "GetMe":
                    return {"id": 123456, "is_bot": True, "first_name": "Bot", "username": "testbot"}
                return True

        self.calls, self.mid, self.n = [], 1000, 0
        self.bot = Bot("123456:TEST", session=FakeSession())
        from bot import build_dispatcher
        self.dp = build_dispatcher(None)

    def _user(self, uid):
        return {"id": uid, "is_bot": False, "first_name": f"User{uid}", "username": f"u{uid}"}

    async def _feed(self, raw):
        from services.kv import kv
        kv._mem = {k: v for k, v in kv._mem.items() if not k.startswith(("thr:", "cb:", "cmd:", "cmdlock:"))}
        self.n += 1
        raw["update_id"] = self.n
        await self.dp.feed_raw_update(self.bot, raw)

    async def say(self, uid, text, chat=GROUP, reply_to=None):
        self.n += 1
        msg = {"message_id": self.n, "date": int(time.time()), "chat": {"id": chat, "type": "supergroup" if chat < 0 else "private", "title": "G"}, "from": self._user(uid),
               "text": text, "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]}
        if reply_to:
            msg["reply_to_message"] = {"message_id": 1, "date": int(time.time()), "chat": {"id": chat, "type": "supergroup" if chat < 0 else "private", "title": "G"},
                                       "from": self._user(reply_to), "text": "hi"}
        self.calls.clear()
        await self._feed({"message": msg})
        return self.sent()

    async def tap(self, uid, data, chat=GROUP):
        self.calls.clear()
        await self._feed({"callback_query": {"id": str(self.n + 1), "from": self._user(uid), "chat_instance": "ci", "data": data,
                                              "message": {"message_id": 5, "date": int(time.time()), "chat": {"id": chat, "type": "supergroup" if chat < 0 else "private", "title": "G"}, "text": "x"}}})
        return self.sent()

    def sent(self):
        return [m.text for n, m in self.calls if n in ("SendMessage", "EditMessageText")]

    def buttons(self):
        out = []
        for n, m in self.calls:
            mk = getattr(m, "reply_markup", None)
            if mk:
                out += [b.callback_data for row in mk.inline_keyboard for b in row if b.callback_data]
        return out

    def alerts(self):
        return [m.text for n, m in self.calls if n == "AnswerCallbackQuery" and m.text]


async def _setup():
    from database.session import db
    from services import users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    from moderation import store as mod_store
    await mod_store.init_schema()                 # group messages pass through the moderation module, which has its own tables
    for uid in (1, 2, 3):
        await users.touch(uid, f"u{uid}", f"User{uid}")
    return db, GroupHarness()


def test_roll_rules():
    from services import dick
    r = random.Random(1)
    assert all(1 <= dick.roll(0, True, r) <= 20 for _ in range(200))
    seen = {dick.roll(20, False, r) for _ in range(500)}
    assert any(x > 0 for x in seen) and any(x < 0 for x in seen)             # both directions happen
    assert all(dick.roll(2, False, r) >= -2 for _ in range(500))             # never below 0 cm


def test_dick_once_per_24h_and_pvp_and_boards():
    async def go():
        from database.models import DickPlayer
        from services import cooldowns, dick
        db, h = await _setup()

        # --- /growdick: works once, then the cooldown answers with the time left
        out = await h.say(1, "/growdick")
        assert out and "cm" in out[0] and "Rank" in out[0]
        out = await h.say(1, "/growdick")
        assert "already changed today" in out[0] and ("24h" in out[0] or "23h" in out[0])
        # after 24 h it works again (move the cooldown into the past)
        async with db.tx() as s:
            await cooldowns.clear(s, 1, "dick")
        out = await h.say(1, "/growdick")
        assert "cm" in out[0] and "already changed" not in out[0]

        # --- give both fighters a known size, then duel
        for uid in (1, 2, 3):
            await h.say(uid, "/growdick")
        async with db.tx() as s:
            for uid, size in ((1, 30), (2, 20), (3, 3)):
                (await s.get(DickPlayer, uid)).size = size
        out = await h.say(1, "/pvp")
        assert "Usage" in out[0]
        out = await h.say(1, "/pvp 99")
        assert "only have" in out[0]
        out = await h.say(1, "/pvp 5")
        cid = h.buttons()[0].split(":")[2]
        assert h.buttons() == [f"dkp:a:{cid}", f"dkp:c:{cid}"]
        await h.tap(1, f"dkp:a:{cid}")
        assert "can't accept your own" in h.alerts()[0]
        await h.tap(3, f"dkp:a:{cid}")                                       # user 3 only has 3 cm
        assert "at least" in h.alerts()[0]
        out = await h.tap(2, f"dkp:a:{cid}")
        assert "wins" in out[0] and "5 cm" in out[0] and "t.me/u" in out[0]  # clickable winner / loser
        a, b = await dick.size_of(1), await dick.size_of(2)
        assert (a, b) in ((35, 15), (25, 25)) and a + b == 50                # exactly 5 cm moved, nothing created
        await h.tap(2, f"dkp:a:{cid}")                                       # double tap: no second payout
        assert "already over" in h.alerts()[0]
        assert await dick.size_of(1) + await dick.size_of(2) == 50

        # --- targeted challenge: only the replied-to player may accept
        await h.say(1, "/pvp 1", reply_to=2)
        cid2 = h.buttons()[0].split(":")[2]
        await h.tap(3, f"dkp:a:{cid2}")
        assert "someone else" in h.alerts()[0]

        # --- leaderboards: group board only has this group's players; overall has everyone, clickable names
        async with db.tx() as s:
            from database.models import User
            await dick._note_chat(s, GROUP2, 3)
        out = await h.say(1, "/dicktop")
        assert "This group" in out[0] and "t.me/u1" in out[0] and "t.me/u3" in out[0]
        assert h.buttons()[:2] == [f"dk:g:0:0", "dk:o:0:0"] or "dk:o:0:0" in h.buttons()
        out = await h.tap(1, "dk:o:0:0")
        assert "Overall" in out[0] and "t.me/u1" in out[0]
        out = await h.tap(2, "dk:g:0:0", chat=GROUP2)                        # a different group sees only its own player(s)
        assert "t.me/u3" in out[0] and "t.me/u1" not in out[0]
        # group order: biggest first
        out = await h.tap(1, "dk:g:0:0")
        assert out[0].index("t.me/u1") < out[0].index("t.me/u3")
        out = await h.say(1, "/dicktop", chat=1)                             # private chat: overall board, no group button
        assert "Overall" in out[0]

    run(go())
