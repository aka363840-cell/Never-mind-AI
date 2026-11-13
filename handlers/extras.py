"""Fishing, mining, trivia and marriage commands."""
from __future__ import annotations

import json
import random
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton as B, Message

from database.models import User
from database.session import db
from keyboards.menus import kb
from services import extras
from services.errors import GameError
from services.kv import kv
from utils import content, deck, gifs
from utils.fmt import esc, mention, num
from utils.targets import resolve_target
from utils.telegram import safe_answer, safe_edit

router = Router()

# (question, options, index of the correct option)
QUESTIONS: list[tuple[str, list[str], int]] = [
    ("What is the capital of Australia?", ["Canberra", "Sydney", "Melbourne", "Perth"], 0),
    ("Which planet is known as the Red Planet?", ["Mars", "Venus", "Jupiter", "Mercury"], 0),
    ("Which is the largest ocean on Earth?", ["Pacific Ocean", "Atlantic Ocean", "Indian Ocean", "Arctic Ocean"], 0),
    ("Who wrote India's national anthem 'Jana Gana Mana'?", ["Rabindranath Tagore", "Bankim Chandra Chatterjee", "Sarojini Naidu", "Premchand"], 0),
    ("What is the chemical symbol for gold?", ["Au", "Ag", "Gd", "Go"], 0),
    ("How many continents are there?", ["7", "5", "6", "8"], 0),
    ("What is the national animal of India?", ["Tiger", "Lion", "Elephant", "Leopard"], 0),
    ("Which is the fastest land animal?", ["Cheetah", "Lion", "Horse", "Leopard"], 0),
    ("Which is the largest mammal?", ["Blue whale", "African elephant", "Giraffe", "Orca"], 0),
    ("What is the square root of 144?", ["12", "14", "11", "13"], 0),
    ("What is the hardest natural substance?", ["Diamond", "Gold", "Iron", "Quartz"], 0),
    ("What is the currency of Japan?", ["Yen", "Won", "Yuan", "Ringgit"], 0),
    ("Who painted the Mona Lisa?", ["Leonardo da Vinci", "Michelangelo", "Pablo Picasso", "Vincent van Gogh"], 0),
    ("What is the capital of Odisha?", ["Bhubaneswar", "Cuttack", "Puri", "Rourkela"], 0),
    ("How many days are there in a leap year?", ["366", "365", "364", "367"], 0),
    ("Which gas do plants absorb from the air for photosynthesis?", ["Carbon dioxide", "Oxygen", "Nitrogen", "Helium"], 0),
    ("In which city is the Taj Mahal?", ["Agra", "Delhi", "Jaipur", "Lucknow"], 0),
    ("What is the smallest prime number?", ["2", "1", "3", "0"], 0),
    ("How many players are on the field for one football (soccer) team?", ["11", "9", "10", "12"], 0),
    ("Which is the largest planet in our solar system?", ["Jupiter", "Saturn", "Neptune", "Earth"], 0),
    ("At sea level, water boils at how many °C?", ["100", "90", "120", "80"], 0),
    ("Who is known as the Father of the Nation in India?", ["Mahatma Gandhi", "Jawaharlal Nehru", "Subhas Chandra Bose", "Sardar Patel"], 0),
    ("What is the chemical formula of water?", ["H2O", "CO2", "O2", "NaCl"], 0),
    ("The Sun is a…", ["Star", "Planet", "Comet", "Moon"], 0),
    ("Who was the first person to walk on the Moon?", ["Neil Armstrong", "Buzz Aldrin", "Yuri Gagarin", "Rakesh Sharma"], 0),
    ("What is the capital of Japan?", ["Tokyo", "Kyoto", "Osaka", "Seoul"], 0),
    ("Which organ pumps blood around the body?", ["Heart", "Liver", "Lungs", "Kidney"], 0),
    ("How many colours are there in a rainbow?", ["7", "5", "6", "8"], 0),
    ("The famous Konark Sun Temple is in which state?", ["Odisha", "Gujarat", "Tamil Nadu", "West Bengal"], 0),
    ("Which is the largest hot desert in the world?", ["Sahara", "Gobi", "Thar", "Kalahari"], 0),
    ("What is 5 in binary?", ["101", "100", "110", "111"], 0),
    ("Which festival is known as the Festival of Lights?", ["Diwali", "Holi", "Eid", "Pongal"], 0),
    ("Who invented the telephone?", ["Alexander Graham Bell", "Thomas Edison", "Nikola Tesla", "Marconi"], 0),
    ("Which is the smallest continent by land area?", ["Australia (Oceania)", "Europe", "Antarctica", "South America"], 0),
    ("How many balls are bowled in one over of cricket?", ["6", "5", "8", "10"], 0),
    ("Which metal is liquid at room temperature?", ["Mercury", "Iron", "Copper", "Zinc"], 0),
    ("What is the capital of India?", ["New Delhi", "Mumbai", "Kolkata", "Chennai"], 0),
    ("Which planet has the most visible rings?", ["Saturn", "Mars", "Earth", "Mercury"], 0),
    ("How many sides does a hexagon have?", ["6", "5", "7", "8"], 0),
    ("Which vitamin do we get from sunlight?", ["Vitamin D", "Vitamin C", "Vitamin A", "Vitamin B12"], 0),
]
QUESTIONS += content.QUESTIONS_MORE          # appended: old question buttons keep working


@router.message(Command("fish", "fishing"))
async def fish(m: Message, db_user: User):
    text, amt = await extras.gather(db_user.id, "fish")
    await m.reply(f"🎣 {text}" + (f"!\nYou sold it for <b>+{num(amt)}</b> 💎" if amt else ""))


@router.message(Command("mine", "mining"))
async def mine(m: Message, db_user: User):
    text, amt = await extras.gather(db_user.id, "mine")
    await m.reply(f"⛏ {text}" + (f"\nYou earned <b>+{num(amt)}</b> 💎" if amt else ""))


# ------------------------------------------------------------ trivia
@router.message(Command("trivia", "quiz"))
async def trivia(m: Message, db_user: User):
    await extras.trivia_start(db_user.id)
    qid = deck.draw("trivia", len(QUESTIONS), m.chat.id)
    q, opts, _ = QUESTIONS[qid]
    order = list(range(len(opts)))
    random.shuffle(order)
    rows = [[B(text=opts[i], callback_data=f"tv:{qid}:{i}:{db_user.id}") for i in order[k:k + 2]] for k in range(0, len(order), 2)]
    await m.reply(f"🧠 <b>Trivia</b> for {mention(db_user.id, db_user.first_name)}\n\n{esc(q)}\n\n<i>Only you can answer. Correct = free 💎!</i>",
                  reply_markup=kb(rows))


@router.callback_query(F.data.startswith("tv:"))
async def trivia_answer(cb: CallbackQuery):
    _, qid, pick, uid = cb.data.split(":")
    if cb.from_user.id != int(uid):
        await safe_answer(cb, "This question isn't yours. Use /trivia for your own!", alert=True)
        return
    q, opts, right = QUESTIONS[int(qid)]
    key = f"tv:{cb.message.chat.id}:{cb.message.message_id}"
    if not await kv.set_nx(key, 3600):
        await safe_answer(cb, "Already answered.")
        return
    if int(pick) == right:
        amt = await extras.trivia_reward(cb.from_user.id, ref=key)
        await safe_edit(cb, f"🧠 <b>Trivia</b>\n\n{esc(q)}\n\n✅ Correct! <b>{esc(opts[right])}</b>\nYou won <b>+{num(amt)}</b> 💎")
    else:
        await safe_edit(cb, f"🧠 <b>Trivia</b>\n\n{esc(q)}\n\n❌ Wrong! You chose <i>{esc(opts[int(pick)])}</i>.\nCorrect answer: <b>{esc(opts[right])}</b>")
    await safe_answer(cb)


# ------------------------------------------------------------ marriage
@router.message(Command("marry", "propose"))
async def marry_cmd(m: Message, command: CommandObject, db_user: User):
    target, _ = await resolve_target(m, (command.args or "").split())
    if target is None:
        raise GameError("💍 Reply to someone (or use @user) to propose: /marry")
    await extras.can_propose(db_user.id, target)
    nonce = secrets.token_urlsafe(6)
    await kv.put(f"mr:{nonce}", json.dumps({"a": db_user.id, "b": target, "an": db_user.first_name}), 180)
    await m.reply(f"💍 {mention(db_user.id, db_user.first_name)} is proposing to <a href=\"tg://user?id={target}\">you</a>!\n"
                  f"The ring costs {num(extras.RING_PRICE)} 💎 (paid by the proposer).\n<i>Expires in 3 minutes.</i>",
                  reply_markup=kb([[B(text="✅ Accept", callback_data=f"mr:ok:{nonce}"),
                                    B(text="✖️ Decline", callback_data=f"mr:no:{nonce}")]]))


@router.callback_query(F.data.startswith("mr:"))
async def marry_cb(cb: CallbackQuery):
    _, verb, nonce = cb.data.split(":")
    raw = await kv.get(f"mr:{nonce}")
    if not raw:
        await safe_edit(cb, "⌛ This proposal expired.")
        await safe_answer(cb)
        return
    d = json.loads(raw)
    if verb == "no":
        if cb.from_user.id not in (d["a"], d["b"]):
            await safe_answer(cb, "This isn't for you.", alert=True)
            return
        await kv.take(f"mr:{nonce}")
        await safe_edit(cb, "💔 The proposal was declined / cancelled.")
        await safe_answer(cb)
        return
    if cb.from_user.id != d["b"]:
        await safe_answer(cb, "Only the person being proposed to can accept 💍", alert=True)
        return
    if not await kv.take(f"mr:{nonce}"):
        await safe_answer(cb)
        return
    await extras.marry(d["a"], d["b"], ref=f"marry:{nonce}")
    await safe_edit(cb, f"💞 <b>Congratulations!</b> {mention(d['a'], d.get('an') or 'The proposer')} and {mention(d['b'], cb.from_user.first_name)} are now married! 🎉")
    await safe_answer(cb)
    if cb.message:
        await gifs.bonus(cb.message, "marry")


@router.message(Command("divorce"))
async def divorce_cmd(m: Message, db_user: User):
    other = await extras.divorce(db_user.id)
    await m.reply(f"💔 {mention(db_user.id, db_user.first_name)} and <a href=\"tg://user?id={other}\">their ex</a> are now divorced.")


@router.message(Command("couple", "marriage", "partner"))
async def couple_cmd(m: Message, db_user: User):
    c = await extras.couple(db_user.id)
    if not c:
        await m.reply("💔 You are not married. Propose with /marry (reply to someone).")
        return
    pid, name, days = c
    await m.reply(f"💞 {mention(db_user.id, db_user.first_name)} ❤️ {mention(pid, name)}\nMarried for <b>{days}</b> day{'s' if days != 1 else ''}.")
