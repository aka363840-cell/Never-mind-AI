from game.waifu_text import bar, caption, left_text, tier


def test_bar_and_tier():
    assert bar(58) == "▰▰▰▰▰▰▱▱▱▱" and bar(1) == "▱" * 10 and bar(100) == "▰" * 10
    assert tier(95).startswith("💍") and tier(58).startswith("💞") and tier(5).startswith("🌱")


def test_left_text():
    assert left_text(17 * 3600 + 12 * 60) == "17h 12m" and left_text(59 * 60) == "59m" and left_text(-5) == "0m"


def test_caption_has_the_pieces():
    c = caption("<b>Kartik</b>", "<b>Auvril</b>", 58, 86400)
    assert "<b>Kartik</b>'s Today's Waifu" in c and "<b>Auvril</b>" in c and "58%" in c and "24h 00m" in c
    assert len(c) < 1024                       # photo captions are capped at 1024 characters
