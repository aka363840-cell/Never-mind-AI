"""Telegram HTML mode needs every literal '&' written as &amp; — static titles must be escaped when rendered."""
import re

BARE_AMP = re.compile(r"&(?!(amp|lt|gt|quot|#\d+);)")


def test_help_and_family_titles_are_html_safe():
    from handlers import views
    from services import family
    from utils import helpcatalog
    for title, _ in helpcatalog.CATEGORIES if hasattr(helpcatalog, "CATEGORIES") else []:
        assert not BARE_AMP.search(views.esc(title))
    for _, (_, gtitle) in family.GROUPS.items():
        assert not BARE_AMP.search(views.esc(gtitle))
    assert "&amp;" in views.esc("Combat & Wealth")


def test_roles_and_achievements_render_escaped():
    import asyncio
    from handlers import family as hf
    sent = []

    class M:
        async def reply(self, text, **k): sent.append(text)
    asyncio.run(hf.roles_cmd(M()))
    assert sent and not BARE_AMP.search(sent[0]) and "Uncles &amp; Aunts" in sent[0]
