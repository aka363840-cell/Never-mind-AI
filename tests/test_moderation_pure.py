"""Pure-logic tests for the moderation module (no Telegram / database needed)."""
import ast
import pathlib
import re
import time
from types import SimpleNamespace as NS

from moderation import defaults as D, matching as M, util as U

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_parse_duration():
    assert U.parse_duration("30s") == 30 and U.parse_duration("15m") == 900 and U.parse_duration("2h") == 7200
    assert U.parse_duration("1d12h") == 86400 + 43200 and U.parse_duration("1w") == 604800 and U.parse_duration("1mo") == 2592000
    assert U.parse_duration("1H") == 3600
    for bad in ("", "spam", "123456", "0m", "-5m", "5 minutes", "h5", "12345678d"):
        assert U.parse_duration(bad) is None, bad
    assert U.parse_duration("99999d") == U.MAX_DURATION


def test_fmt_duration():
    assert U.fmt_duration(None) == "permanent" and U.fmt_duration(90) == "1m 30s" and U.fmt_duration(90061) == "1d 1h 1m"


def test_render_is_not_str_format():
    out = U.render("Hi {first}! {unknown} {{x}} {0}", first="Ann")
    assert out == "Hi Ann! {unknown} {{x}} {0}"


def test_buttons():
    txt, rows = U.extract_buttons("Welcome!\n[Rules](buttonurl://t.me/rules)\n[Site](buttonurl://example.com:same)\n[X](buttonurl://https://x.org)")
    assert txt == "Welcome!" and rows == [[("Rules", "https://t.me/rules"), ("Site", "https://example.com")], [("X", "https://x.org")]]
    assert U.extract_buttons("no buttons")[1] == []


def test_strip_command():
    assert U.strip_command("/setrules@MyBot  <b>No spam</b>") == "<b>No spam</b>"
    assert U.strip_command("!setrules x") == "x"


def test_hosts_and_links():
    assert M.extract_hosts("see https://WWW.Example.com/a?b=1 and bit.ly/xyz") == ["example.com", "bit.ly"]
    assert M.extract_hosts("file.txt and e.g. nothing") == []
    assert M.extract_hosts("hidden", ["https://evil.xyz/p"]) == ["evil.xyz"]
    assert M.domain_matches("a.b.example.com", "example.com") and not M.domain_matches("notexample.com", "example.com")
    assert "shortener" in M.link_flags("bit.ly") and "ip-address" in M.link_flags("1.2.3.4") and "punycode" in M.link_flags("xn--abc.com")
    assert "telegram-invite" in M.link_flags("t.me", "https://t.me/+AbCdEf")
    assert M.link_flags("t.me", "https://t.me/somechannel") == []


def test_repeated_chars_and_mentions():
    assert M.has_repeated_chars("a" + "b" * 15, 15) and not M.has_repeated_chars("abababababababab", 15)
    assert not M.has_repeated_chars("hello   world", 3) and not M.has_repeated_chars("x", 15)
    assert M.count_mentions("@alice @bob_1 mail@x.com") == 2


def test_filter_matching():
    fl = [dict(id=1, kind="word", pattern="spam", action="delete"), dict(id=2, kind="phrase", pattern="free money", action="warn"),
          dict(id=3, kind="domain", pattern="scam.com", action="ban"), dict(id=4, kind="link", pattern="/join/", action="delete")]
    m = lambda t, h=(): (M.match_filters(fl, t, list(h)) or {}).get("id")      # noqa: E731
    assert m("this is SPAM!") == 1 and m("spammer") is None and m("get FREE   money now") == 2
    assert m("visit", ["pay.scam.com"]) == 3 and m("x", ["scamx.com"]) is None
    assert m("s\u200bpam") == 1                      # zero-width evasion
    assert m("ｓｐａｍ") == 1                          # full-width letters
    assert m("hello") is None


def test_sliding_counter_and_duplicates():
    c = M.SlidingCounter()
    assert [c.hit("k", t, 10) for t in (0, 1, 2, 3)] == [1, 2, 3, 4] and c.hit("k", 20, 10) == 1
    d = M.DuplicateTracker()
    assert d.check(1, 1, "buy now", 0, 60) == (1, 1) and d.check(1, 1, "BUY  now", 5, 60) == (2, 1)
    assert d.check(1, 2, "buy now", 6, 60) == (1, 2) and d.check(1, 3, "buy now", 7, 60)[1] == 3
    assert d.check(2, 1, "buy now", 8, 60) == (1, 1)                  # other chat: independent
    assert d.check(1, 1, "buy now", 500, 60)[0] == 1                  # window passed


def test_suspicion():
    s, why = M.suspicion("Crypto t.me/scam", None, "hi", None, [])
    assert s >= 3 and "link in profile name" in why
    s, _ = M.suspicion("Bob", None, "earn money click here", 10, ["bit.ly"], ("https://bit.ly/x",))
    assert s >= 3
    assert M.suspicion("Alice", "alice", "good morning everyone", 3600, [])[0] == 0


def test_defaults_are_all_off_and_merge_safe():
    d = D.with_defaults(None)
    for k in ("flood", "spam", "antibot", "antiforward", "antilink", "antiraid", "captcha"):
        assert d[k]["on"] is False, k
    assert d["slowmode"] == 0 and d["locks"] == {} and d["welcome"]["on"] is False and not d["lockdown"]["on"]
    merged = D.with_defaults({"flood": {"on": True}, "bogus": 1, "warn": {"limit": 5}})
    assert merged["flood"]["on"] and merged["flood"]["limit"] == 8 and merged["warn"]["limit"] == 5 and "bogus" not in merged
    D.with_defaults({"flood": {"on": True}})["flood"]["limit"] = 99
    assert D.DEFAULTS["flood"]["limit"] == 8                      # defaults never mutated


def test_group_isolation_of_settings_objects():
    a, b = D.with_defaults({"rules": "A"}), D.with_defaults({"rules": "B"})
    a["locks"]["text"] = True
    assert b["locks"] == {} and D.DEFAULTS["locks"] == {}


def test_lock_names_and_permissions():
    assert D.lock_name("Stickers") == "stickers" and D.lock_name("gif") == "gifs" and D.lock_name("files") == "documents" and D.lock_name("zzz") is None
    cur = {f: True for f in D.ALL_PERM_FIELDS}
    p = D.compute_permissions(cur, {"photos": True})
    assert p["can_send_photos"] is False and p["can_send_videos"] is True and p["can_send_messages"] is True
    p = D.compute_permissions(cur, {"media": True, "videos": False})
    assert not p["can_send_photos"] and not p["can_send_documents"] and not p["can_send_voice_notes"] and p["can_send_polls"]
    p = D.compute_permissions({**cur, "can_change_info": False}, {})
    assert p["can_change_info"] is False                            # unmanaged fields stay as the admins set them
    p = D.compute_permissions({**cur, "can_send_photos": False}, {"photos": False}, {"photos": True})
    assert p["can_send_photos"] is True                             # a lock WE had set and now removed gives the permission back
    p = D.compute_permissions({**cur, "can_send_photos": False, "can_invite_users": False}, {"text": True})
    assert p["can_send_photos"] is False and p["can_invite_users"] is False and p["can_send_messages"] is False   # hand-set permissions survive other locks
    assert not any(D.compute_permissions(cur, {"all": True})[f] for f in D.SEND_FIELDS)
    assert D.compute_permissions({}, {})["can_send_messages"] is True


def _msg(**kw):
    base = dict(text=None, photo=None, video=None, audio=None, document=None, animation=None, sticker=None, voice=None, video_note=None,
                poll=None, contact=None, location=None, venue=None, dice=None, game=None, forward_origin=None, forward_date=None)
    base.update(kw)
    return NS(**base)


def test_kinds_and_locked_by():
    assert D.kinds_of(_msg(text="hi")) == {"text"}
    assert D.kinds_of(_msg(sticker=NS(is_animated=True, is_video=False))) == {"sticker", "animated_sticker"}
    assert D.kinds_of(_msg(animation=object(), document=object())) == {"gif"}      # a GIF is not also a "document"
    assert "forward" in D.kinds_of(_msg(text="x", forward_origin=object()))
    assert D.locked_by({"stickers": True}, {"sticker"}, False) == "stickers" and D.locked_by({"stickers": True}, {"text"}, False) is None
    assert D.locked_by({"links": True}, {"text"}, True) == "links" and D.locked_by({"links": True}, {"text"}, False) is None
    assert D.locked_by({"media": True}, {"photo"}, False) == "media" and D.locked_by({"media": True}, {"sticker"}, False) is None
    assert D.locked_by({"animations": True}, {"gif"}, False) == "animations"
    assert D.locked_by({"all": True}, {"text"}, False) == "all" and D.locked_by({"text": False}, {"text"}, False) is None
    assert D.locked_by({"invite": True}, {"text"}, False) is None             # permission-only locks never delete messages


def test_presets():
    base = D.with_defaults(None)
    hi = D.apply_preset(base, "high")
    assert hi["protection"] == "high" and hi["flood"]["on"] and hi["captcha"]["mode"] == "math" and hi["slowmode"] == 3 and hi["antiforward"]["on"]
    assert base["flood"]["on"] is False                                       # input untouched
    off = D.apply_preset(hi, "off")
    assert not any(off[k]["on"] for k in ("flood", "spam", "antibot", "antiforward", "antilink", "antiraid", "captcha")) and off["slowmode"] == 0
    assert off["locks"] == hi["locks"] and off["rules"] == hi["rules"]       # presets never touch locks / rules / lists


def test_ai_guard_patterns():
    src = (ROOT / "moderation" / "guard.py").read_text()
    ns: dict = {"re": re}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in ("_STRONG", "_WEAK", "_TARGET", "_PERSON", "_HINDI", "REQUEST"):
            exec(compile(ast.Module([node], []), "g", "exec"), ns)
    rx = ns["REQUEST"]
    for yes in ("please ban him", "kick this guy", "mute @spammer now", "isko ban kar do", "ban karo", "can you mute everyone", "promote me", "unban him", "ban kar do", "delete him", "remove the spammer", "kick everyone"):
        assert rx.search(yes), yes
    for no in ("what a banner", "I got banned yesterday lol", "nice pin", "the lock on my door", "remove the bug from my code", "good morning", "delete the file", "lock the door", "pin it on the map", "mute the music tab", "unlock the achievement"):
        assert not rx.search(no), no


def test_no_hardcoded_identities():
    for f in (ROOT / "moderation").glob("*.py"):
        assert "8445198778" not in f.read_text(), f.name


def test_legacy_greeting_stays_unless_group_wrote_its_own():
    st = D.with_defaults(None)
    assert not D.legacy_suppressed(st, "join") and not D.legacy_suppressed(st, "leave")
    st["captcha"]["on"] = True
    assert not D.legacy_suppressed(st, "join")                      # captcha alone must not silence the default welcome
    st["welcome"].update(on=True, text="")
    assert not D.legacy_suppressed(st, "join")                      # enabled but empty text -> default stays
    st["welcome"].update(text="Hi {first}")
    assert D.legacy_suppressed(st, "join") and not D.legacy_suppressed(st, "leave")
    st["goodbye"].update(on=True, text="Bye")
    assert D.legacy_suppressed(st, "leave")
    st["welcome"]["on"] = False
    assert not D.legacy_suppressed(st, "join")                      # switched off again -> default greeting returns


def test_free_form_settings_survive_roundtrip():
    st = D.with_defaults({"locks": {"text": True, "stickers": True}, "lockdown": {"on": True, "until": 5, "prev": {"can_send_messages": True}}})
    assert st["locks"] == {"text": True, "stickers": True} and st["lockdown"]["prev"] == {"can_send_messages": True}
    assert D.with_defaults(st) == st                                   # idempotent: load -> save -> load changes nothing


def test_link_filter_matches_plain_text():
    fl = [dict(id=1, kind="link", pattern="t.me/+", action="delete")]
    assert M.match_filters(fl, "join t.me/+abc now", ["t.me"]) is not None and M.match_filters(fl, "hello", []) is None
