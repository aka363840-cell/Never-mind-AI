"""GamePix feed items are validated, mapped to Ella categories and listed without being scanned (no network)."""


def _cat(tmp_path, monkeypatch):
    for k, v in {"ELLA_DEV": "1", "ELLA_BOT_TOKEN": "x", "ELLA_SESSION_SECRET": "s" * 48, "ELLA_DATA_DIR": str(tmp_path),
                       "ELLA_GAMEPIX_SID": "9HOS3", "ELLA_PUBLIC_URL": "http://127.0.0.1", "ELLA_EMBED_HOSTS": "gamezipper.com"}.items():
        monkeypatch.setenv(k, v)
    from ella_server import config
    from ella_server.catalogue import Catalogue
    return Catalogue(config.load())


def test_gamepix_items(tmp_path, monkeypatch):
    c = _cat(tmp_path, monkeypatch)
    c.load_gamepix([
        {"namespace": "Stack-Ball", "title": "Stack Ball", "category": "arcade", "image": "https://img.gamepix.com/a.png", "quality_score": 9},
        {"namespace": "cards-1", "title": "Solitaire", "category": "cards", "image": "https://evil.example/a.png"},
        {"namespace": "bad ns", "title": "x"},
    ])
    assert sorted(c.games) == ["gp-cards-1", "gp-stack-ball"]
    assert c.games["gp-cards-1"]["thumbnail"] == "" and c.games["gp-cards-1"]["categories"] == ["card"]
    assert c.embed_url("gp-stack-ball") == "https://play.gamepix.com/Stack-Ball/embed?sid=9HOS3"
    assert set(c.eligible_slugs()) == set(c.games) and c.scan_slugs() == []
    assert "play.gamepix.com" in c.st.embed_hosts
