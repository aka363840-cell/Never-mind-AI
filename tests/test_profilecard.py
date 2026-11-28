from utils import profilecard


def test_render_png():
    png = profilecard.render(name="Ella ♡ 💎 Test", level=12, xp=40, xp_need=100, diamonds=12345, kills=3, deaths=1,
                             wins=2, title="Boss", pet="Cat", clan="Stars", badges=4, badges_total=30, streak=5)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_render_edge_values():
    png = profilecard.render(name="", level=1, xp=0, xp_need=0, diamonds=0, kills=0, deaths=0, wins=0)
    assert png.startswith(b"\x89PNG")
