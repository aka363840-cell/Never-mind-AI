"""/app launcher for Ella Gaming: both entry points (the /app command and the Games-hub button) build the SAME Mini App button.
Needs aiogram (the bot's own dependency); skipped where it is not installed."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("aiogram")

from handlers import ella_app  # noqa: E402

URL = "https://games.example.org/"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ELLA_GAMING_URL", URL)
    monkeypatch.delenv("ELLA_GAMING_LINK", raising=False)


def _button(markup):
    return markup.inline_keyboard[0][0]


def test_private_chat_gets_a_mini_app_button():
    b = _button(ella_app.launch_markup(True))
    assert b.web_app is not None and b.web_app.url == URL and b.url is None


def test_group_chat_gets_a_plain_link_never_a_web_app_button(monkeypatch):
    b = _button(ella_app.launch_markup(False, "EllaBot"))
    assert b.web_app is None and b.url == "https://t.me/EllaBot"
    monkeypatch.setenv("ELLA_GAMING_LINK", "https://t.me/EllaBot/play")
    assert _button(ella_app.launch_markup(False, "EllaBot")).url == "https://t.me/EllaBot/play"
    monkeypatch.setenv("ELLA_GAMING_LINK", "https://evil.example/x")          # only t.me direct links are accepted
    assert _button(ella_app.launch_markup(False, "EllaBot")).url == "https://t.me/EllaBot"


def test_not_configured_or_not_https(monkeypatch):
    for bad in ("", "http://games.example.org/", "javascript:alert(1)", "games.example.org"):
        monkeypatch.setenv("ELLA_GAMING_URL", bad)
        assert ella_app.launch_markup(True) is None


def test_hub_variant_adds_a_back_row_and_same_url():
    m = ella_app.launch_markup(True, back_row=[ella_app.B(text="⬅️ Games", callback_data="ar:hub")])
    assert m.inline_keyboard[0][0].web_app.url == URL and m.inline_keyboard[1][0].callback_data == "ar:hub"


def test_command_and_hub_button_open_the_same_url(monkeypatch):
    sent = {}

    class Msg:
        chat = SimpleNamespace(type="private")
        bot = None

        async def reply(self, text, reply_markup=None):
            sent["cmd"] = reply_markup

    async def fake_edit(cb, text, markup=None):
        sent["hub"] = markup

    async def fake_answer(cb, *a, **k):
        pass

    monkeypatch.setattr(ella_app, "safe_edit", fake_edit)
    monkeypatch.setattr(ella_app, "safe_answer", fake_answer)
    cb = SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(type="private")), bot=None)
    asyncio.run(ella_app.app_cmd(Msg()))
    asyncio.run(ella_app.app_from_hub(cb))
    assert sent["cmd"].inline_keyboard[0][0].web_app.url == sent["hub"].inline_keyboard[0][0].web_app.url == URL


def test_hub_has_the_button_and_help_lists_the_command():
    import inspect

    from handlers import arcade
    from utils import helpcatalog as H
    assert 'callback_data="ella:app"' in inspect.getsource(arcade.hub)
    assert "app" in dict(H.DESC_LIST) and "app" not in H.MENU                 # in /help, not in the "/" menu
