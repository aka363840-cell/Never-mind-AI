import { h, icon } from "./dom.js";
import { tg } from "./tg.js";
import { api, login, ApiError } from "./api.js";
import { premiumEntry, openPremium, startParam, resumeParam } from "./premium.js?v=6";

const $ = (s) => document.querySelector(s);
const view = $("#view"), nav = $("#nav"), root = $("#overlay-root"), toastEl = $("#toast");

const CAT_NAMES = { arcade: "Arcade", puzzle: "Puzzle", racing: "Racing", sports: "Sports", action: "Action", adventure: "Adventure", strategy: "Strategy", board: "Board Games", casual: "Casual", multiplayer: "Multiplayer", card: "Card", word: "Word" };
const S = {
  tab: "home", player: null, favs: new Set(), cats: [], home: null, homeAt: 0, homeDirty: true, scroll: {},
  games: { q: "", category: "", items: [], total: 0, next: 0, loading: false, error: null, gen: 0, verifying: 0, done: false },
  top: { q: "", category: "", items: [], total: 0, next: 0, loading: false, error: null, gen: 0, verifying: 0, done: false },
};
let sheetClose = null;
let layers = [];          // open overlays, top = last; each entry closes itself
let renderToken = 0;
const PCTX = { pushLayer: (f) => pushLayer(f), popLayer: (f) => popLayer(f), toast: (m) => toast(m), avatar: (p) => avatar(p) };

// ------------------------------------------------------------------ small helpers
function toast(msg, ms = 2600) {
  toastEl.textContent = msg; toastEl.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => (toastEl.hidden = true), ms);
}
const catName = (g) => CAT_NAMES[(g.categories || [])[0]] || "Game";
function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 90) return "just now"; if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " d ago";
}
function errMsg(e) {
  if (!(e instanceof ApiError)) return "Something went wrong.";
  if (e.code === "network") return "No connection. Check your internet and try again.";
  if (e.code === "rate_limited") return "Too many requests - give it a moment.";
  return e.message || "Something went wrong.";
}
function avatar(p, big = false) {
  const initial = ((p && (p.display_name || p.first_name)) || "E").trim().charAt(0).toUpperCase() || "E";
  const box = h("div", { class: "avatar" + (big ? " lg" : "") }, initial);
  if (p && p.photo_url) {
    const img = h("img", { class: "avatar" + (big ? " lg" : ""), src: p.photo_url, alt: "", referrerpolicy: "no-referrer", decoding: "async" });
    img.addEventListener("error", () => img.replaceWith(box));
    return img;
  }
  return box;
}
function stateBox(ico, title, text, action) {
  return h("div", { class: "state" }, icon(ico), h("h3", {}, title), h("p", {}, text), action || null);
}
function skeletons(n, cls = "skel") { return Array.from({ length: n }, () => h("div", { class: cls })); }

// ------------------------------------------------------------------ favourites
function applyFav(slug, on) {
  on ? S.favs.add(slug) : S.favs.delete(slug);
  document.querySelectorAll("[data-fav]").forEach((b) => { if (b.dataset.fav === slug) b.setAttribute("aria-pressed", String(on)); });
}
async function toggleFav(slug) {
  const on = !S.favs.has(slug);
  applyFav(slug, on); tg.haptic("light");
  S.homeDirty = true;
  try { await api.setFav(slug, on); if (S.tab === "profile") renderProfile(true); }
  catch (e) { applyFav(slug, !on); toast(errMsg(e)); }
}
function heartBtn(slug, cls = "heart") {
  return h("button", { class: cls, dataset: { fav: slug }, "aria-label": "Favourite", "aria-pressed": String(S.favs.has(slug)), onclick: (ev) => { ev.stopPropagation(); toggleFav(slug); } }, icon("heart"));
}

// ------------------------------------------------------------------ cards
function thumb(g) {
  const t = h("div", { class: "thumb" }, g.emoji || "🎮");
  if (g.thumbnail) {
    const img = h("img", { src: g.thumbnail, alt: "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("load", () => img.classList.add("ready"));
    img.addEventListener("error", () => img.remove());
    t.append(img);
  }
  return t;
}
function card(g, kind = "grid") {
  const hit = h("button", { class: "card-hit", "aria-label": `${g.name}, ${catName(g)}`, onclick: () => openSheet(g) });
  if (kind === "feat") {
    const t = thumb(g);
    t.append(h("div", { class: "shade" }, h("span", { class: "pill" }, catName(g)), h("div", { class: "name" }, g.name)));
    hit.append(t);
  } else {
    hit.append(thumb(g), h("div", { class: "meta" }, h("div", { class: "name" }, g.name), h("div", { class: "sub" }, catName(g))));
  }
  return h("div", { class: "card " + (kind === "grid" ? "" : kind === "feat" ? "feat" : "sm") }, hit, heartBtn(g.slug));
}
const rail = (games, kind) => h("div", { class: "rail" }, games.map((g) => card(g, kind)));
function section(title, body, more) {
  return h("section", { class: "section" }, h("div", { class: "sec-head" }, h("h2", {}, title), more || null), body);
}

// ------------------------------------------------------------------ navigation
const TABS = [["home", "Home"], ["games", "Games"], ["premium", "Trending"], ["profile", "Profile"]];
function buildNav() {
  nav.querySelectorAll(".nav-btn").forEach((b) => {
    const [id, label] = TABS.find((t) => t[0] === b.dataset.tab);
    b.replaceChildren(icon(id), h("span", {}, label));
    b.addEventListener("click", () => { tg.haptic("light"); closeAllLayers(); setTab(id); });
  });
  nav.hidden = false;
}
function syncNav() { nav.querySelectorAll(".nav-btn").forEach((b) => { if (b.dataset.tab === S.tab) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); }); }
function syncBack() { tg.setBack(layers.length || S.tab !== "home" ? goBack : null); }
function goBack() {
  if (layers.length) { layers[layers.length - 1](); return; }
  if (S.tab !== "home") setTab("home");
}
function pushLayer(closeFn) { layers.push(closeFn); syncBack(); }
function popLayer(closeFn) { layers = layers.filter((f) => f !== closeFn); syncBack(); }
function closeAllLayers() { [...layers].reverse().forEach((f) => f()); }

function setTab(tab) {
  S.scroll[S.tab] = window.scrollY;
  S.tab = tab; syncNav(); syncBack();
  renderToken++;
  ({ home: renderHome, games: () => renderGames(), premium: () => renderGames(true), profile: renderProfile })[tab]();
  if (tab !== "home") requestAnimationFrame(() => window.scrollTo(0, S.scroll[tab] || 0)); else window.scrollTo(0, S.scroll.home || 0);
}

// ------------------------------------------------------------------ HOME
function greeting() {
  const hr = new Date().getHours();
  return hr < 5 ? "Still up" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
}
function topbar() {
  return h("div", { class: "topbar" },
    h("div", { class: "brand" }, h("img", { src: "assets/logo.svg", alt: "" }), h("span", {}, "Ella ", h("b", {}, "Gaming"))),
    h("button", { "aria-label": "Profile", onclick: () => setTab("profile") }, avatar(S.player)));
}
function searchBox(value, onInput, onEnter, autofocus = false) {
  const input = h("input", { type: "search", placeholder: "Search games", "aria-label": "Search games", enterkeyhint: "search", autocomplete: "off", autocapitalize: "off", spellcheck: "false", maxlength: "60", value: value || "" });
  const clear = h("button", { class: "clear", "aria-label": "Clear search", hidden: !value, onclick: () => { input.value = ""; clear.hidden = true; onInput(""); input.focus(); } }, icon("close"));
  let t;
  input.addEventListener("input", () => { clear.hidden = !input.value; if (onInput) { clearTimeout(t); t = setTimeout(() => onInput(input.value.trim()), 250); } });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); input.blur(); if (onEnter) onEnter(input.value.trim()); } });
  if (autofocus) setTimeout(() => input.focus(), 50);
  return h("div", { class: "search" }, icon("search"), input, clear);
}
async function loadHome(force = false) {
  if (!force && S.home && !S.homeDirty && Date.now() - S.homeAt < 30000) return S.home;
  const d = await api.home();
  S.home = d; S.homeAt = Date.now(); S.homeDirty = false; S.cats = d.categories;
  S.favs = new Set(d.favorite_slugs);
  return d;
}
async function renderHome() {
  const tok = renderToken;
  const name = (S.player && S.player.display_name) || "player";
  const head = [topbar(),
    h("div", { class: "greet" }, h("small", {}, greeting() + ","), h("h1", {}, h("span", {}, name))),
    h("p", { class: "tagline" }, "Free games to play."),
    premiumEntry(PCTX),
    searchBox("", null, (q) => goGames({ q }))];
  const body = h("div", {});
  view.replaceChildren(...head, body);
  if (!S.home) body.replaceChildren(h("div", { class: "section" }, h("div", { class: "rail" }, skeletons(2, "skel feat-skel"))));
  try {
    const d = await loadHome();
    if (tok !== renderToken) return;
    body.replaceChildren(...homeSections(d));
  } catch (e) {
    if (tok !== renderToken) return;
    body.replaceChildren(stateBox("offline", "Can't load games", errMsg(e), h("button", { class: "btn", onclick: renderHome }, icon("refresh"), "Try again")));
  }
}
function homeSections(d) {
  const out = [];
  if (d.verifying) out.push(h("div", { class: "notice section" }, `Checking games for ads - ${d.verifying} left. More games appear as they pass.`));
  if (d.featured.length) out.push(section("Featured", rail(d.featured, "feat")));
  if (d.total_games) out.push(h("button", { class: "browse", onclick: () => goGames({}) }, h("span", { class: "txt" }, h("b", {}, `Browse all ${d.total_games} games`), h("small", {}, "Search or filter by category")), icon("sparkle")));
  if (d.recent.length) out.push(section("Continue playing", rail(d.recent, "sm")));
  out.push(section("Categories", h("div", { class: "cats" }, d.categories.filter((c) => c.count > 0).map((c) =>
    h("button", { class: "cat", onclick: () => goGames({ category: c.id }) }, h("span", { class: "ico" }, icon(c.icon)), h("span", { class: "txt" }, h("b", {}, c.name), h("small", {}, `${c.count} games`)))))));
  if (d.popular.length) out.push(section("Popular", rail(d.popular, "sm"), h("button", { onclick: () => goGames({}) }, `All ${d.total_games} games`)));
  (d.rails || []).forEach((r) => out.push(section(r.name, rail(r.items, "sm"), h("button", { onclick: () => goGames({ category: r.id }) }, `See all ${r.count}`))));
  out.push(section("Favourites", d.favorites.length ? rail(d.favorites, "sm")
    : h("div", { class: "empty-card" }, icon("heart"), h("span", {}, "Tap the heart on any game to keep it here."))));
  if (!d.total_games) out.push(stateBox("sparkle", "No games yet", "The catalogue is still being checked. Try again in a minute."));
  return out;
}
function goGames({ q = "", category = "" }) {
  const g = S.games;
  if (g.q !== q || g.category !== category || !g.items.length) { Object.assign(g, { q, category, items: [], total: 0, next: 0, error: null, done: false, gen: g.gen + 1, loading: false }); }
  setTab("games");
}

// ------------------------------------------------------------------ GAMES
function renderGames(top = false) {
  const g = top ? S.top : S.games, tok = renderToken;
  const chipRow = h("div", { class: "chips", role: "group", "aria-label": "Categories" });
  const fill = () => {
    const cats = (S.cats.length ? S.cats : Object.entries(CAT_NAMES).map(([id, name]) => ({ id, name, count: 1 }))).filter((c) => c.count > 0);
    chipRow.replaceChildren(h("button", { class: "chip", "aria-pressed": String(!g.category), onclick: () => setCategory("") }, "All"),
      ...cats.map((c) => h("button", { class: "chip", "aria-pressed": String(g.category === c.id), onclick: () => setCategory(c.id) }, c.name)));
  };
  const setCategory = (id) => { if (g.category === id) return; Object.assign(g, { category: id, items: [], total: 0, next: 0, error: null, done: false, gen: g.gen + 1 }); fill(); redraw(); loadMore(); };
  const onQuery = (q) => { if (q === g.q) return; Object.assign(g, { q, items: [], total: 0, next: 0, error: null, done: false, gen: g.gen + 1 }); redraw(); loadMore(); };
  const sticky = h("div", { class: "sticky" }, searchBox(g.q, onQuery, onQuery), chipRow);
  const results = h("div", {});
  view.replaceChildren(...(top ? [h("p", { class: "tagline" }, "Best games, picked by quality."), premiumEntry(PCTX)] : []), top ? searchBox(g.q, onQuery, onQuery) : sticky, results);
  if (!top) fill();
  if (!top && !S.cats.length) loadHome().then(() => { if (tok === renderToken) fill(); }).catch(() => {});

  function redraw() {
    const kids = [];
    if (g.verifying) kids.push(h("div", { class: "notice" }, `Still checking some games for ads (${g.verifying} left) - more will show up soon.`));
    if (!g.loading && !g.error && g.done || g.items.length) kids.push(h("div", { class: "count" }, top && !g.q ? `${g.total + 3} premium games` : g.total === 1 ? "1 game" : `${g.total} games`));
    if (g.items.length) kids.push(h("div", { class: "grid" }, g.items.map((x) => card(x))));
    if (g.loading && !g.items.length) kids.push(h("div", { class: "grid" }, skeletons(6)));
    if (g.error) kids.push(stateBox("offline", g.items.length ? "Couldn't load more" : "Can't load games", g.error, h("button", { class: "btn", onclick: () => { g.error = null; redraw(); loadMore(); } }, icon("refresh"), "Try again")));
    else if (g.done && !g.items.length) kids.push(stateBox("search", "No games found", g.q ? `Nothing matches "${g.q}". Try another word or category.` : "This category is empty right now."));
    const sentinel = h("div", { class: "sentinel" });
    kids.push(sentinel);
    if (g.loading && g.items.length) kids.push(h("div", { class: "more" }, h("div", { class: "spinner" })));
    results.replaceChildren(...kids);
    if (g.next != null && !g.error && g.items.length) io.observe(sentinel);
  }
  const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) loadMore(); }, { rootMargin: "700px" });
  async function loadMore() {
    if (g.loading || g.error || g.done || tok !== renderToken) return;
    const gen = g.gen; g.loading = true; redraw();
    try {
      const p = { limit: "24", offset: String(g.next || 0) };
      if (g.q) p.q = g.q; if (g.category) p.category = g.category; if (top) p.top = "1";
      const r = await api.games(p);
      if (gen !== g.gen) return;
      g.items.push(...r.items); g.total = r.total; g.next = r.next_offset; g.done = r.next_offset == null; g.verifying = r.verifying || 0;
    } catch (e) {
      if (gen !== g.gen) return;
      g.error = errMsg(e);
    } finally { if (gen === g.gen) { g.loading = false; if (tok === renderToken) redraw(); } }
  }
  g.loading = false; redraw(); if (!g.items.length || g.error) { g.error = null; loadMore(); }
}

// ------------------------------------------------------------------ GAME SHEET
function openSheet(g) {
  closeSheet();
  const close = () => { backdrop.remove(); sheet.remove(); popLayer(close); sheet.dataset.closed = "1"; if (sheetClose === close) sheetClose = null; };
  const backdrop = h("div", { class: "backdrop", onclick: close });
  const tagBox = h("div", { class: "tags" }, (g.categories || []).map((c) => h("span", { class: "pill" }, CAT_NAMES[c] || c)));
  const playBtn = h("button", { class: "btn", onclick: () => { close(); openPlayer(g); } }, icon("play"), "Play now");
  const sheet = h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": g.name, id: "sheet" },
    h("div", { class: "grab" }), h("div", { class: "hero" }, thumb(g)), h("h2", {}, g.name), tagBox,
    h("div", { class: "actions" }, playBtn,
      h("button", { class: "sq", dataset: { fav: g.slug }, "aria-label": "Favourite", "aria-pressed": String(S.favs.has(g.slug)), onclick: () => toggleFav(g.slug) }, icon("heart"))),
    h("p", { class: "fine" }, icon("shield"), "Free to play. Games may show their own ads. Controls differ per game."));
  root.append(backdrop, sheet); sheetClose = close; pushLayer(close);
  api.game(g.slug).then((d) => {                       // details (tags) + confirms the game is still available
    if (sheet.dataset.closed) return;
    const extra = (d.tags || []).filter((t) => t.length < 22).slice(0, 4);
    tagBox.append(...extra.map((t) => h("span", { class: "pill" }, t)));
  }).catch((e) => {
    if (sheet.dataset.closed) return;
    if (e.status === 404) { playBtn.disabled = true; playBtn.textContent = "No longer available"; }
  });
}
function closeSheet() { if (sheetClose) sheetClose(); }

// ------------------------------------------------------------------ PLAYER
const LOAD_TIMEOUT_MS = globalThis.ELLA_LOAD_TIMEOUT_MS || 25000, PING_MS = globalThis.ELLA_PING_MS || 45000;   // overridable only for tests
function openPlayer(g) {
  const P = { sid: null, iframe: null, timer: null, hb: null, closed: false, fs: false };
  const title = h("div", { class: "p-title" }, g.name);
  const cover = h("div", { class: "p-cover" });
  const stage = h("div", { class: "p-stage" });
  const fsBtn = h("button", { class: "ib", "aria-label": "Fullscreen", hidden: true, onclick: toggleFs }, icon("expand"));
  const bar = h("div", { class: "p-bar" },
    h("button", { class: "back", onclick: close }, icon("back"), "Games"), title, fsBtn,
    h("button", { class: "ib", "aria-label": "Reload game", onclick: start }, icon("refresh")));
  const offline = h("div", { class: "offline", hidden: true }, h("span", {}, "Connection lost"), h("button", { onclick: () => { offline.hidden = true; start(); } }, "Reload game"));
  const el = h("div", { class: "player", role: "dialog", "aria-modal": "true", "aria-label": "Playing " + g.name }, bar, stage);
  stage.append(offline, cover);
  root.append(el); nav.hidden = true;
  if (tg.canFullscreen() || el.requestFullscreen || stage.requestFullscreen) fsBtn.hidden = false;

  function showCover(kind, msg) {
    if (kind === "loading") cover.replaceChildren(h("div", { class: "spinner" }), h("p", {}, `Loading ${g.name}...`));
    else cover.replaceChildren(icon("alert", "big"), h("h3", {}, kind === "gone" ? "Game unavailable" : "Couldn't load the game"), h("p", {}, msg),
      h("div", { class: "actions-row" }, kind === "gone" ? null : h("button", { class: "btn", onclick: start }, icon("refresh"), "Try again")),
      h("button", { class: "btn ghost", onclick: close }, "Back to games"));
    cover.hidden = false;
  }
  async function start() {
    if (P.closed) return;
    clearTimeout(P.timer); clearInterval(P.hb); endSession();
    if (P.iframe) { P.iframe.src = "about:blank"; P.iframe.remove(); P.iframe = null; }
    offline.hidden = true; showCover("loading");
    if (!navigator.onLine) { showCover("error", "You're offline. Reconnect and try again."); return; }
    let s;
    try { s = await api.startSession(g.slug, tg.platform); }
    catch (e) { if (!P.closed) showCover(e.status === 404 ? "gone" : "error", e.status === 404 ? "This game was removed from Ella Gaming." : errMsg(e)); return; }
    if (P.closed) return;
    P.sid = s.session_id;
    const f = h("iframe", {
      title: g.name, src: s.play_url, allow: "fullscreen; autoplay; gamepad; accelerometer; gyroscope",
      // no allow-popups / allow-top-navigation: a game can neither open pop-ups nor redirect the app
      sandbox: "allow-scripts allow-same-origin allow-pointer-lock allow-forms", referrerpolicy: "strict-origin-when-cross-origin",
    });
    f.addEventListener("load", () => { if (f !== P.iframe) return; clearTimeout(P.timer); cover.hidden = true; });
    P.iframe = f; stage.prepend(f);
    P.timer = setTimeout(() => { if (!cover.hidden) showCover("error", "The game is taking too long to load. Check your connection and try again."); }, LOAD_TIMEOUT_MS);
    P.hb = setInterval(() => { if (P.sid && document.visibilityState === "visible" && navigator.onLine) api.ping(P.sid).catch(() => {}); }, PING_MS);
  }
  function endSession() { if (P.sid) { api.end(P.sid).catch(() => {}); P.sid = null; } }
  function toggleFs() {
    if (tg.canFullscreen()) { tg.isFullscreen() ? tg.exitFullscreen() : tg.requestFullscreen(); return; }
    const doc = document;
    if (doc.fullscreenElement) doc.exitFullscreen(); else (el.requestFullscreen || stage.requestFullscreen).call(el.requestFullscreen ? el : stage).catch(() => toast("Fullscreen isn't available here."));
  }
  const syncFs = () => { const on = tg.isFullscreen() || !!document.fullscreenElement; fsBtn.replaceChildren(icon(on ? "shrink" : "expand")); };
  const onOffline = () => { offline.hidden = false; };
  const onOnline = () => { if (!offline.hidden) toast("Back online"); };
  const onHide = () => { if (P.sid) api.end(P.sid).catch(() => {}); };
  function close() {
    if (P.closed) return; P.closed = true;
    clearTimeout(P.timer); clearInterval(P.hb); endSession();
    if (P.iframe) P.iframe.src = "about:blank";
    el.remove(); nav.hidden = false; tg.playing(false); tg.exitFullscreen();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    window.removeEventListener("offline", onOffline); window.removeEventListener("online", onOnline); window.removeEventListener("pagehide", onHide);
    document.removeEventListener("fullscreenchange", syncFs);
    popLayer(close); S.homeDirty = true;
    if (S.tab === "home") renderHome();
  }
  window.addEventListener("offline", onOffline); window.addEventListener("online", onOnline); window.addEventListener("pagehide", onHide);
  document.addEventListener("fullscreenchange", syncFs); tg.onFullscreenChange(syncFs);
  pushLayer(close); tg.playing(true); tg.haptic("medium");
  start();
}

// ------------------------------------------------------------------ PROFILE
async function renderProfile(quiet = false) {
  const tok = renderToken;
  if (!quiet) view.replaceChildren(h("div", { class: "p-head" }, avatar(S.player, true), h("h1", {}, (S.player && S.player.display_name) || "Player")), h("div", { class: "section grid" }, skeletons(2)));
  let d;
  try { d = await api.me(); } catch (e) {
    if (tok !== renderToken) return;
    view.replaceChildren(stateBox("offline", "Can't load your profile", errMsg(e), h("button", { class: "btn", onclick: () => renderProfile() }, icon("refresh"), "Try again"))); return;
  }
  if (tok !== renderToken) return;
  S.player = d.player; S.favs = new Set(d.favorite_slugs);
  const p = d.player;
  const input = h("input", { type: "text", maxlength: "24", placeholder: "Nickname", "aria-label": "Nickname", value: p.nickname || "", autocomplete: "off" });
  const save = h("button", { class: "btn", onclick: async () => {
    save.disabled = true;
    try { const r = await api.patchMe(input.value.trim()); S.player = r.player; S.homeDirty = true; toast("Nickname saved"); renderProfile(true); }
    catch (e) { toast(errMsg(e)); } finally { save.disabled = false; }
  } }, "Save");
  const hist = d.history.map((x) => {
    const row = h("button", { class: "row" + (x.available ? "" : " off"), disabled: !x.available, onclick: () => openSheet(x.game) },
      h("div", { class: "t" }, x.game.thumbnail ? h("img", { src: x.game.thumbnail, alt: "", loading: "lazy", referrerpolicy: "no-referrer" }) : null),
      h("div", { class: "grow" }, h("b", {}, x.game.name), h("small", {}, x.available ? "Played " + ago(x.started_at) : "No longer available")),
      icon("play"));
    row.querySelector("svg").setAttribute("width", "18");
    return row;
  });
  view.replaceChildren(
    h("div", { class: "p-head" }, avatar(p, true), h("h1", {}, p.display_name), p.username ? h("div", { class: "handle" }, "@" + p.username) : null),
    h("div", { class: "field" }, input, save),
    section("Favourites", d.favorites.length ? h("div", { class: "grid" }, d.favorites.map((g) => card(g))) : h("div", { class: "empty-card" }, icon("heart"), h("span", {}, "No favourites yet. Tap the heart on a game."))),
    section("Recently played", hist.length ? h("div", { class: "list" }, hist) : h("div", { class: "empty-card" }, icon("clock"), h("span", {}, "Games you launch will show up here."))),
    h("div", { class: "about" }, h("b", {}, "Ella Gaming"), " is a separate game space. Your profile, favourites and history here are stored only for Ella Gaming - they are not linked to anything else in the bot."));
}

// ------------------------------------------------------------------ boot
function gate(title, text, retry) {
  nav.hidden = true;
  view.replaceChildren(h("div", { class: "gate" }, h("img", { src: "assets/logo.svg", alt: "Ella Gaming" }), h("h1", {}, title), h("p", {}, text),
    retry ? h("button", { class: "btn", onclick: retry }, icon("refresh"), "Try again") : null));
}
async function boot() {
  tg.init();
  if (!tg.available) { gate("Open in Telegram", "Ella Gaming is a Telegram Mini App. Send /app to the bot, or tap its Ella Gaming button, to play."); return; }
  view.replaceChildren(h("div", { class: "gate" }, h("img", { src: "assets/logo.svg", alt: "" }), h("div", { class: "spinner" })));
  try {
    const r = await login();
    S.player = r.player;
  } catch (e) {
    if (e.code === "expired" || e.code === "bad_signature" || e.code === "bad_init_data") gate("Please reopen the app", "Your Telegram session has expired. Close this window and open Ella Gaming again from the bot.");
    else gate("Can't sign in", errMsg(e), boot);
    return;
  }
  buildNav(); setTab("home");
  const sp = startParam() || resumeParam(); if (sp) openPremium(PCTX, sp);
}
boot();
