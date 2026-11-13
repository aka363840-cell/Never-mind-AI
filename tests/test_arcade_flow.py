"""End-to-end arcade tests: real Dispatcher + middlewares + handlers, fake Telegram transport.
Run: pytest tests/test_arcade_flow.py"""
import asyncio
import os
import tempfile
import time

DB = os.path.join(tempfile.gettempdir(), "arcade_flow_test.db")
URL = f"sqlite+aiosqlite:///{DB}"


def run(coro):
    return asyncio.run(coro)


class Harness:
    """Feeds raw Telegram updates into the bot and records everything the bot sends back."""

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
                    return {"message_id": outer.mid, "date": int(time.time()), "chat": {"id": method.chat_id, "type": "private"}, "text": method.text}
                return True

        self.calls: list = []
        self.mid = 1000
        self.n = 0
        self.bot = Bot("123456:TEST", session=FakeSession())
        from bot import build_dispatcher
        self.dp = build_dispatcher(None)

    def _user(self, uid):
        return {"id": uid, "is_bot": False, "first_name": f"User{uid}", "username": f"u{uid}"}

    async def _feed(self, raw):
        from services.kv import kv
        kv._mem = {k: v for k, v in kv._mem.items() if not k.startswith(("thr:", "cb:"))}   # flood / double-tap guards are not under test here
        self.n += 1
        raw["update_id"] = self.n
        await self.dp.feed_raw_update(self.bot, raw)

    async def say(self, uid, text, reply_to=None):
        self.n += 1
        msg = {"message_id": self.n, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "from": self._user(uid), "text": text,
               "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]}
        if reply_to:
            msg["reply_to_message"] = {"message_id": 1, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "from": self._user(reply_to), "text": "hi"}
        self.calls.clear()
        await self._feed({"message": msg})
        return self.sent()

    async def tap(self, uid, data, mid=None):
        self.calls.clear()
        await self._feed({"callback_query": {"id": str(self.n + 1), "from": self._user(uid), "chat_instance": "ci", "data": data,
                                              "message": {"message_id": mid or self.mid, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "text": "x"}}})
        return self.sent()

    def sent(self) -> list[str]:
        return [m.text for n, m in self.calls if n in ("SendMessage", "EditMessageText")]

    def buttons(self) -> list[str]:
        out = []
        for n, m in self.calls:
            mk = getattr(m, "reply_markup", None)
            if mk:
                out += [b.callback_data for row in mk.inline_keyboard for b in row if b.callback_data]
        return out

    def alerts(self) -> list[str]:
        return [m.text for n, m in self.calls if n == "AnswerCallbackQuery" and m.text]


async def _setup():
    from database.session import db
    from services import economy, users
    if os.path.exists(DB):
        os.remove(DB)
    db.init(URL)
    await db.create_all()
    for uid in (1, 2):
        await users.touch(uid, f"u{uid}", f"User{uid}")
        async with db.tx() as s:
            await economy.apply(s, uid, wallet=50_000, kind="seed", ref=f"seed:{uid}")
    return db, Harness()


def test_every_new_command_answers_and_buttons_work():
    async def go():
        from services import arcade, economy
        db, h = await _setup()

        out = await h.say(1, "/arcade")
        assert any("Arcade" in t for t in out) and "ar:sc" in h.buttons()
        out = await h.tap(1, "nav:arcade")                                   # reachable from the main menu page router too
        assert any("Arcade" in t for t in out)

        # --- scratch card: costs 100, reveal works, tapping someone else's card is refused
        before = (await economy.balance(1)).balance
        out = await h.say(1, "/scratch")
        assert any("Scratch card" in t for t in out)
        gid = h.buttons()[0].split(":")[1]
        assert (await economy.balance(1)).balance <= before - arcade.SCRATCH_COST + 3000
        await h.tap(2, f"sc:{gid}:0")
        assert h.alerts() and "isn't your card" in h.alerts()[0]
        out = await h.tap(1, f"sc:{gid}:a")
        assert out and "Scratch card" in out[0] and ("Three of a kind" in out[0] or "No match" in out[0])

        # --- rock paper scissors (free + bet)
        out = await h.say(1, "/rps")
        assert any("free practice" in t for t in out)
        out = await h.tap(1, "rp:1:0:r")
        assert "You:" in out[0] and "Me:" in out[0]
        out = await h.say(1, "/rps 200")
        out = await h.tap(1, "rp:1:200:p")
        assert "Rock" in out[0] or "Paper" in out[0]
        await h.tap(2, "rp:1:0:r")
        assert h.alerts() and "isn't yours" in h.alerts()[0]

        # --- tic-tac-toe: open lobby, join, X wins on the top row
        out = await h.say(1, "/ttt")
        gid = h.buttons()[0].split(":")[1]
        assert h.buttons()[0].startswith("tj:")
        await h.tap(1, f"tj:{gid}")
        assert "Wait for another player" in h.alerts()[0]
        out = await h.tap(2, f"tj:{gid}")
        assert "User2" in out[0]
        for uid, cell in ((1, 0), (2, 3), (1, 1), (2, 4)):
            await h.tap(uid, f"ttt:{gid}:{cell}")
            await asyncio.sleep(0.3)                                         # human pace (the double-tap guard is 0.25 s per player)
        await h.tap(2, f"ttt:{gid}:8")                                       # not O's turn any more -> refused, nothing breaks
        assert "not your turn" in h.alerts()[0]
        out = await h.tap(1, f"ttt:{gid}:2")
        assert "wins" in out[0] and "User1" in out[0]
        st1 = await arcade.stats_of(1)
        assert st1["ttt_win"] == 1 and st1["ttt_play"] == 1
        out = await h.tap(1, f"ttt:{gid}:5")                                 # game is over -> friendly expiry message
        assert "expired" in out[0]

        # --- hangman: play every letter, the game must end cleanly (solved or game over) and never pay twice
        out = await h.say(1, "/hangman")
        assert any("Hangman" in t for t in out)
        gid = h.buttons()[0].split(":")[1]
        last = ""
        for L in arcade.ALPHABET:
            res = await h.tap(1, f"hm:{gid}:{L}")
            if res:
                last = res[0]
            if "Solved" in last or "Game over" in last:
                break
        assert "Solved" in last or "Game over" in last, last
        res = await h.tap(1, f"hm:{gid}:A")
        assert res and "expired" in res[0]

        # --- would you rather: votes are counted, one vote per person (changeable)
        out = await h.say(1, "/wyr")
        gid = h.buttons()[0].split(":")[1]
        await h.tap(1, f"wy:{gid}:0")
        await h.tap(2, f"wy:{gid}:1")
        out = await h.tap(2, f"wy:{gid}:0")
        assert "2 votes" in out[0]

        # --- riddle / fact / horoscope / fortune
        out = await h.say(1, "/riddle")
        idx = [b for b in h.buttons() if b.startswith("rd:")][0]
        out = await h.tap(1, idx)
        assert "Answer" in out[0]
        assert any("Did you know" in t for t in await h.say(1, "/fact"))
        assert any("Leo" in t for t in await h.say(1, "/horoscope leo"))
        await h.say(1, "/horoscope")
        out = await h.tap(1, "ho:Virgo")
        assert "Virgo" in out[0]
        assert any("fortune" in t.lower() for t in await h.say(1, "/fortune"))
        out = await h.say(1, "/horoscope dragon")
        assert any("don't know that sign" in t for t in out)

        # --- reputation (reply) + cooldown + board
        out = await h.say(1, "/rep", reply_to=2)
        assert any("+1 reputation" in t for t in out)
        out = await h.say(1, "/rep", reply_to=2)
        assert any("cooldown" in t for t in out)
        out = await h.say(2, "/rep", reply_to=1)
        assert any("+1 reputation" in t for t in out)
        assert any("Top reputation" in t for t in await h.say(1, "/toprep"))

        # --- bank interest: refused when the bank is small
        out = await h.say(1, "/interest")
        assert any("Interest starts" in t for t in out)
        async with db.tx() as s:
            await economy.apply(s, 1, wallet=-10_000, bank=10_000, kind="deposit", ref="dep")
        out = await h.say(1, "/interest")
        assert any("+<b>100</b>" in t for t in out), out

        # --- hub buttons that start games
        for b in ("ar:rd", "ar:fc", "ar:fo", "ar:ho", "ar:tr", "ar:wy", "ar:ttt"):
            assert await h.tap(1, b), b
        await h.bot.session.close()
        await db.dispose()
    run(go())
