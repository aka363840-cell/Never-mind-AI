"""Browser end-to-end tests (headless Chromium, emulated Pixel 7 = Android Chrome, touch input) against the REAL Ella server
and a LOCAL fake GameZipper. The Telegram SDK (telegram.org/js/telegram-web-app.js) is replaced by a small mock that
exposes the same WebApp API surface and a correctly SIGNED initData - so the real backend auth runs end-to-end.
What this does NOT prove: behaviour inside the real Telegram clients, or against the real gamezipper.com."""
from __future__ import annotations

import json
import os
import re
import time

import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import BOT_TOKEN, Srv, free_port, make_init_data

USER = {"id": 4242, "first_name": "Ada", "last_name": "Lovelace", "username": "ada_l", "language_code": "en"}

TG_MOCK = """
(function(){
  const handlers = {};
  const W = { initData: %(init)s, initDataUnsafe: { user: %(user)s }, platform: 'android', version: '8.0', isFullscreen: false,
    isVersionAtLeast(v){ return parseFloat(v) <= 8.0; }, ready(){ window.__ready = true; }, expand(){ window.__expanded = true; },
    setHeaderColor(){}, setBackgroundColor(){}, setBottomBarColor(){},
    BackButton: { _f: null, visible: false, onClick(f){ this._f = f; }, offClick(f){ if (this._f === f) this._f = null; }, show(){ this.visible = true; }, hide(){ this.visible = false; } },
    HapticFeedback: { impactOccurred(){} },
    disableVerticalSwipes(){ window.__swipes = false; }, enableVerticalSwipes(){ window.__swipes = true; },
    enableClosingConfirmation(){ window.__confirm = true; }, disableClosingConfirmation(){ window.__confirm = false; },
    requestFullscreen(){ this.isFullscreen = true; (handlers.fullscreenChanged || []).forEach(f => f()); },
    exitFullscreen(){ this.isFullscreen = false; (handlers.fullscreenChanged || []).forEach(f => f()); },
    onEvent(n, f){ (handlers[n] = handlers[n] || []).push(f); }, close(){ window.__closed = true; } };
  window.Telegram = { WebApp: W };
})();
"""


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    import fake_upstream
    from ella_server import config
    from ella_server.app import create_app
    up = Srv(fake_upstream.make_app(), free_port()).start()
    os.environ.update({
        "ELLA_ADS_POLICY": "verified_only", "ELLA_DEV": "1", "ELLA_BOT_TOKEN": BOT_TOKEN, "ELLA_SESSION_SECRET": "s" * 48, "ELLA_DATA_DIR": str(tmp_path_factory.mktemp("e2e")),
        "ELLA_CATALOGUE_URL": f"{up.url}/api/games.json", "ELLA_EMBED_HOSTS": f"127.0.0.1:{up.port}", "ELLA_SCAN_ON_START": "1",
        "ELLA_SCAN_BROWSER": "0", "ELLA_RATE_IP_PER_MIN": "100000", "ELLA_RATE_AUTH_PER_MIN": "1000", "ELLA_PUBLIC_URL": "http://127.0.0.1"})
    app = create_app(config.load())
    ella = Srv(app, free_port()).start()
    for _ in range(300):
        sc = app.state.scanner
        if app.state.cat.games and not sc.running and sc.total and sc.done >= sc.total:
            break
        time.sleep(0.1)
    yield ella, up
    ella.stop(); up.stop()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield p, b
        b.close()


def new_page(browser, ella, telegram=True, user=USER, device="Pixel 7"):
    p, b = browser
    ctx = b.new_context(**p.devices[device])
    page = ctx.new_page()
    reqs: list[str] = []
    page.on("request", lambda r: reqs.append(r.url))
    popups: list = []
    ctx.on("page", lambda pg: popups.append(pg) if pg != page else None)
    page.route(re.compile(r"https://fonts\.(googleapis|gstatic)\.com/.*"), lambda r: r.fulfill(status=200, body="", content_type="text/css"))
    if telegram:
        js = TG_MOCK % {"init": json.dumps(make_init_data(user)), "user": json.dumps(user)}
        page.route("https://telegram.org/js/telegram-web-app.js", lambda r: r.fulfill(status=200, body=js, content_type="application/javascript"))
    else:
        page.route("https://telegram.org/js/telegram-web-app.js", lambda r: r.fulfill(status=200, body="", content_type="application/javascript"))
    page.add_init_script("window.ELLA_LOAD_TIMEOUT_MS = 2500; window.ELLA_PING_MS = 1000;")
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "ERR_" not in m.text and "Failed to load resource" not in m.text else None)
    page.ella_reqs, page.ella_popups, page.ella_errors, page.ctx = reqs, popups, errors, ctx
    return page


def tab(page, name):
    page.locator(f'.nav-btn[data-tab="{name}"]').click()


def test_outside_telegram_shows_gate(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella, telegram=False)
    page.goto(ella.url)
    expect(page.locator(".gate h1")).to_have_text("Open in Telegram")
    assert page.locator("#nav").is_hidden()
    assert not [r for r in page.ella_reqs if "/api/" in r]          # no API traffic without a signed login


def test_home_and_navigation(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    expect(page.locator(".greet h1")).to_contain_text("Ada")                              # personalised greeting
    expect(page.locator(".topbar .avatar")).to_be_visible()                                # avatar
    expect(page.locator(".brand")).to_contain_text("Ella")
    for title in ("Featured", "Categories", "Popular", "Favourites"):
        expect(page.locator(".sec-head h2", has_text=title).first).to_be_visible()
    expect(page.locator(".card.feat").first).to_be_visible()
    expect(page.locator(".search input")).to_be_visible()
    # bottom navigation: exactly Home / Games / Premium / Profile
    btns = page.locator("#nav .nav-btn")
    assert btns.count() == 4 and [t.strip() for t in btns.all_inner_texts()] == ["Home", "Games", "Premium", "Profile"]
    body = page.locator("body").inner_text().lower()
    assert "leaderboard" not in body and "stats" not in body
    assert page.evaluate("window.__ready && window.__expanded")
    tab(page, "games"); expect(page.locator(".grid .card").first).to_be_visible()
    tab(page, "profile"); expect(page.locator(".p-head h1")).to_have_text("Ada")
    tab(page, "home"); expect(page.locator(".greet h1")).to_contain_text("Ada")
    assert not page.ella_errors, [e[:300] for e in page.ella_errors]
    page.ctx.close()


def test_games_catalogue_lazy_loading_search_categories(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    tab(page, "games")
    expect(page.locator(".grid .card").first).to_be_visible()
    assert page.locator(".grid .card").count() == 24                                       # first page only (lazy)
    expect(page.locator(".count")).to_have_text("54 games")
    for _ in range(6):                                                                     # scroll to trigger infinite loading
        page.mouse.wheel(0, 4000); page.wait_for_timeout(300)
    expect(page.locator(".grid .card")).to_have_count(54)
    names = page.locator(".grid .card .name").all_inner_texts()
    assert len(set(names)) == 54 and not any("Duplicate" in n or "Evil" in n for n in names)
    # search
    page.locator(".sticky .search input").fill("star")
    expect(page.locator(".count")).not_to_have_text("54 games")
    n = page.locator(".grid .card").count()
    assert 0 < n < 54 and page.locator(".grid .card .name").first.inner_text().lower().startswith("star")
    page.locator(".sticky .search input").fill("zzqq")
    expect(page.locator(".state h3")).to_have_text("No games found")
    page.locator(".search .clear").click()
    expect(page.locator(".grid .card").first).to_be_visible()
    # category filter
    page.locator(".chip", has_text="Racing").click()
    expect(page.locator(".chip[aria-pressed=true]")).to_have_text("Racing")
    for _ in range(40):                                  # (poll from Python: the page's strict CSP forbids eval-based waits)
        if 0 < page.locator(".grid .card").count() < 54:
            break
        page.wait_for_timeout(100)
    assert 0 < page.locator(".grid .card").count() < 54
    page.locator(".chip", has_text="All").click()
    expect(page.locator(".count")).to_have_text("54 games")
    # home category tile -> filtered games
    tab(page, "home"); page.locator(".cat", has_text="Puzzle").click()
    expect(page.locator(".chip[aria-pressed=true]")).to_have_text("Puzzle")
    assert not page.ella_errors, [e[:300] for e in page.ella_errors]
    page.ctx.close()


def test_play_game_touch_controls_no_ads_back_navigation_history(browser, stack):
    ella, up = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    tab(page, "games")
    page.locator(".grid .card-hit").first.click()
    expect(page.locator("#sheet")).to_be_visible()                                         # game details sheet
    expect(page.locator("#sheet h2")).not_to_be_empty()
    page.locator("#sheet .btn", has_text="Play now").click()
    player = page.locator(".player")
    expect(player).to_be_visible()
    iframe = page.locator(".player iframe")
    expect(iframe).to_be_visible()
    src = iframe.get_attribute("src")
    assert src.startswith(up.url) and "utm_medium=embed" in src and "utm_campaign=ella-gaming" in src
    sandbox = iframe.get_attribute("sandbox")
    assert "allow-scripts" in sandbox and "allow-popups" not in sandbox and "allow-top-navigation" not in sandbox
    assert page.evaluate("window.__swipes === false && window.__confirm === true")        # swipe-to-close disabled while playing
    assert page.evaluate("window.Telegram.WebApp.BackButton.visible")
    expect(page.locator(".p-cover")).to_be_hidden()                                        # loading state finished
    # touch controls inside the game iframe: tap the canvas, score changes
    fl = page.frame_locator(".player iframe")
    expect(fl.locator("#score")).to_have_text("score: 0")
    box = fl.locator("#c").bounding_box()
    for _ in range(3):
        page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    expect(fl.locator("#score")).to_have_text("score: 3")
    # fullscreen button uses Telegram's fullscreen API
    page.locator('.player [aria-label="Fullscreen"]').click()
    assert page.evaluate("window.Telegram.WebApp.isFullscreen")
    page.locator('.player [aria-label="Fullscreen"]').click()
    assert not page.evaluate("window.Telegram.WebApp.isFullscreen")
    # session heartbeat is measured while open
    page.wait_for_timeout(2300)
    assert any("/ping" in r for r in page.ella_reqs)
    # no advertising traffic, no popups, no external redirects
    hosts = {re.sub(r"^https?://([^/]+).*", r"\1", r) for r in page.ella_reqs}
    assert hosts <= {f"127.0.0.1:{ella.port}", f"127.0.0.1:{up.port}", "telegram.org", "fonts.googleapis.com", "fonts.gstatic.com"}, hosts
    assert not page.ella_popups
    # back to games via Telegram's BackButton and via the in-app button
    page.evaluate("window.Telegram.WebApp.BackButton._f()")
    expect(player).to_have_count(0)
    assert page.evaluate("window.__swipes === true && window.__confirm === false")
    expect(page.locator("#nav")).to_be_visible()
    page.locator(".grid .card-hit").first.click(); page.locator("#sheet .btn", has_text="Play now").click()
    expect(page.locator(".player iframe")).to_be_visible()
    page.locator(".player .back").click()
    expect(page.locator(".player")).to_have_count(0)
    # history + recent show up in profile / home; favourites persist
    page.locator(".grid .card .heart").nth(1).click()
    tab(page, "profile")
    expect(page.locator(".row").first).to_be_visible()
    expect(page.locator(".grid .card").first).to_be_visible()                              # favourite
    tab(page, "home")
    expect(page.locator(".sec-head h2", has_text="Continue playing")).to_be_visible()
    page.reload()
    expect(page.locator(".sec-head h2", has_text="Continue playing")).to_be_visible()      # survives reload (server-side)
    expect(page.locator(".sec-head h2", has_text="Favourites")).to_be_visible()
    expect(page.locator(".empty-card")).to_have_count(0)
    assert not page.ella_errors, [e[:300] for e in page.ella_errors]
    page.ctx.close()


def test_slow_game_shows_loading_then_failure_state_with_retry(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    tab(page, "games")
    page.locator(".sticky .search input").fill("slow burner")           # server-side search finds the slow fixture game
    card = page.locator(".grid .card-hit", has_text="Slow Burner").first
    expect(card).to_be_visible()
    card.click(); page.locator("#sheet .btn", has_text="Play now").click()
    expect(page.locator(".p-cover .spinner")).to_be_visible()                             # loading state
    expect(page.locator(".p-cover h3")).to_have_text("Couldn't load the game", timeout=6000)   # 2.5 s test timeout < 6 s server delay
    expect(page.locator(".p-cover .btn", has_text="Try again")).to_be_visible()
    page.locator(".p-cover .btn", has_text="Back to games").click()
    expect(page.locator(".player")).to_have_count(0)
    page.ctx.close()


def test_connection_interruption_while_playing(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    tab(page, "games"); page.locator(".grid .card-hit").first.click(); page.locator("#sheet .btn", has_text="Play now").click()
    expect(page.locator(".p-cover")).to_be_hidden()
    page.ctx.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    expect(page.locator(".offline")).to_be_visible()
    page.ctx.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.locator(".offline button").click()
    expect(page.locator(".player iframe")).to_be_visible()
    expect(page.locator(".p-cover")).to_be_hidden()
    page.ctx.close()


def test_api_failure_shows_error_and_recovers(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    state = {"fail": True}
    def handler(route):
        if state["fail"]:
            route.fulfill(status=503, body=json.dumps({"error": "catalogue_unavailable", "message": "The game catalogue is loading or temporarily unavailable."}), content_type="application/json")
        else:
            route.continue_()
    page.route("**/api/home", handler)
    page.goto(ella.url)
    expect(page.locator(".state h3")).to_have_text("Can't load games")
    state["fail"] = False
    page.locator(".state .btn").click()
    expect(page.locator(".card.feat").first).to_be_visible()
    page.ctx.close()


def test_expired_session_token_relogs_in_silently(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    hit = {"n": 0}
    def handler(route):
        hit["n"] += 1
        if hit["n"] == 1:
            route.fulfill(status=401, body=json.dumps({"error": "token_expired"}), content_type="application/json")
        else:
            route.continue_()
    page.route("**/api/home", handler)
    page.goto(ella.url)
    expect(page.locator(".card.feat").first).to_be_visible()
    assert sum(1 for r in page.ella_reqs if r.endswith("/api/auth/telegram")) == 2     # logged in again, transparently
    page.ctx.close()


def test_profile_nickname_and_two_users_are_separate(browser, stack):
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url); tab(page, "profile")
    page.locator(".field input").fill("Countess")
    page.locator(".field .btn").click()
    expect(page.locator(".p-head h1")).to_have_text("Countess")
    page.ctx.close()
    other = new_page(browser, ella, user={"id": 5151, "first_name": "Grace"})
    other.goto(ella.url); tab(other, "profile")
    expect(other.locator(".p-head h1")).to_have_text("Grace")
    expect(other.locator(".empty-card").first).to_be_visible()                          # no favourites / history of the other user
    other.ctx.close()


def test_layout_on_small_android_no_horizontal_scroll(browser, stack):
    ella, _ = stack
    for device in ("Galaxy S9+", "Pixel 5", "iPad Mini"):
        page = new_page(browser, ella, device=device)
        page.goto(ella.url)
        expect(page.locator(".card.feat").first).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), device
        tab(page, "games"); expect(page.locator(".grid .card").first).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), device
        page.ctx.close()


def test_screenshots_for_report(browser, stack):
    ella, _ = stack
    out = os.path.join(os.path.dirname(__file__), "..", "docs", "screens")
    os.makedirs(out, exist_ok=True)
    page = new_page(browser, ella)
    page.goto(ella.url); expect(page.locator(".card.feat").first).to_be_visible(); page.wait_for_timeout(700)
    page.screenshot(path=f"{out}/home.png")
    tab(page, "games"); expect(page.locator(".grid .card").first).to_be_visible(); page.wait_for_timeout(500)
    page.screenshot(path=f"{out}/games.png")
    page.locator(".grid .card-hit").first.click(); page.wait_for_timeout(500)
    page.screenshot(path=f"{out}/game-sheet.png")
    page.locator("#sheet .btn", has_text="Play now").click(); expect(page.locator(".p-cover")).to_be_hidden(); page.wait_for_timeout(300)
    page.screenshot(path=f"{out}/player.png")
    page.locator(".player .back").click(); tab(page, "profile"); page.wait_for_timeout(600)
    page.screenshot(path=f"{out}/profile.png")
    page.ctx.close()


def test_premium_refresh_and_resume(browser, stack):
    """Refreshing the Mini App during a game drops you back into it; Back leads to a Resume card; a table you left is not re-joined."""
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    tab(page, "premium")
    page.locator(".tile.ludo .go").click()
    page.get_by_text("🤖 Add computer").click()
    page.get_by_role("button", name="Start game").click()
    expect(page.locator(".board")).to_be_visible()
    code = page.evaluate("JSON.parse(localStorage.getItem('ella.pg.last')).code")
    page.reload()                                                     # refresh in the middle of the game
    expect(page.locator(".board")).to_be_visible()
    expect(page.locator(".pg-top h1")).to_contain_text("LUDO")
    page.locator(".pg-top .ibtn").first.click()                       # Back -> hub with a Resume card
    expect(page.locator(".resume")).to_be_visible()
    page.locator(".resume").click()
    expect(page.locator(".board")).to_be_visible()
    page.locator(".pg-top .ibtn").first.click()
    page.locator(".pg-top .ibtn").first.click()                       # close premium; nothing to resume after a deliberate Back
    page.reload()
    expect(page.locator(".greet h1")).to_be_visible()
    assert page.locator(".pg").count() == 0
    assert not page.ella_errors, [e[:300] for e in page.ella_errors]


def test_premium_vs_computer(browser, stack):
    """Premium Games: add a computer player, start each game, play a move, the computer answers; no JS errors."""
    ella, _ = stack
    page = new_page(browser, ella)
    page.goto(ella.url)
    tab(page, "premium")
    shots = os.environ.get("PG_SHOTS")

    def snap(name):
        if shots:
            page.screenshot(path=f"{shots}/{name}.png")

    def table(game):
        page.locator(f".tile.{game} .go").click()
        expect(page.locator(".code")).to_be_visible()
        page.get_by_text("🤖 Add computer").click()
        expect(page.locator(".seat .avatar.bot")).to_be_visible()

    # --- chess with a 3+2 clock
    table("chess")
    page.get_by_role("button", name="3+2").click()
    snap("chess_lobby")
    page.get_by_role("button", name="Start game").click()
    expect(page.locator(".board.cb")).to_be_visible()
    expect(page.locator(".clk").first).to_contain_text(":")
    for _ in range(40):                                              # if the computer has White it moves first
        if page.locator(".hint", has_text="Tap a piece").count():
            break
        page.wait_for_timeout(300)
    # play the first legal pawn move by driving the board: click a piece that has moves, then a target
    moved = False
    for sq in page.locator(".cb .hit").all():
        sq.click()
        if page.locator(".cb circle[fill='#6fe3b0']").count():
            bb = page.locator(".cb circle[fill='#6fe3b0']").first.bounding_box()
            page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
            moved = True
            break
    assert moved
    expect(page.locator(".moves span").first).to_be_visible()
    page.wait_for_timeout(300)
    for _ in range(60):                                              # the computer replies
        if page.locator(".moves span").count() >= 2:
            break
        page.wait_for_timeout(300)
    assert page.locator(".moves span").count() >= 2
    snap("chess_play")
    expect(page.locator(".hint", has_text="Tap a piece")).to_be_visible()      # my turn again
    before = page.locator(".moves span").count()
    page.get_by_role("button", name="↩️ Undo").click()               # computer allows the takeback instantly
    page.wait_for_timeout(500)
    assert page.locator(".moves span").count() == before - 2
    page.get_by_role("button", name="🏳 Resign").click(); page.get_by_role("button", name="Resign").last.click()
    expect(page.locator(".win-t")).to_be_visible()
    page.get_by_role("button", name="Leave table").click()

    # --- uno with house rules
    table("uno")
    page.get_by_text("7-0: swap & rotate hands").click(); page.get_by_text("Jump-in").click()
    snap("uno_lobby")
    page.get_by_role("button", name="Start game").click()
    expect(page.locator(".hand .uc").first).to_be_visible()
    snap("uno_play")
    page.get_by_role("button", name="Leave table").count()
    page.locator(".pg-top .ibtn").first.click()

    # --- ludo: dice animation then the computer plays on its own
    table("ludo")
    snap("ludo_lobby")
    page.get_by_role("button", name="Start game").click()
    expect(page.locator(".board")).to_be_visible()
    for _ in range(40):
        if page.locator(".dice.can").count():
            break
        page.wait_for_timeout(300)
    page.locator(".dice").click(force=True)
    page.wait_for_timeout(1200)
    snap("ludo_play")
    assert page.locator(".dice b.on").count() >= 1
    page.locator(".pg-top .ibtn").first.click()

    # --- settings sheet changes the board / card back
    page.locator('button[aria-label="Look and sound settings"]').first.click()
    page.get_by_role("button", name="Rose gold").click()
    assert page.evaluate("document.querySelector('.pg').dataset.cb") == "3"
    page.get_by_role("button", name="Done").click()
    assert not page.ella_errors, [e[:300] for e in page.ella_errors]
