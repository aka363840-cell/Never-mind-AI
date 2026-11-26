// Premium Games (UNO + LUDO) for Ella Gaming. Self-contained: only uses the existing login/api helpers.
import { h, icon } from "./dom.js";
import { tg } from "./tg.js";
import { call } from "./api.js";
import { prefs, sfx, buzz, notify, themeAttrs, settingsSheet } from "./pg_fx.js";

const NS = "http://www.w3.org/2000/svg";
const S = (t, a = {}, ...k) => { const e = document.createElementNS(NS, t); for (const [n, v] of Object.entries(a)) e.setAttribute(n, v); k.flat().forEach((x) => x && e.append(x)); return e; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NAMES = { uno: "UNO", ludo: "LUDO", chess: "CHESS" };
const CAP = { p: "♟", n: "♞", b: "♝", r: "♜", q: "♛" };
const PIECE = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
const GLYPH = { s: "⊘", r: "⇄", d: "+2", "4": "+4", "": "" };
const TOKEN_COLORS = [["red", "#ff6b6b", "#c4161f"], ["green", "#46dd86", "#0f8a47"], ["yellow", "#ffe066", "#e0a400"], ["blue", "#5b98ff", "#1743b8"]];

// Remember the table you are in (survives refresh) and the tables you left on purpose (so an old invite link never drags you back in).
const LS = { get(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} } };
const lastRoom = { get: () => { const l = LS.get("ella.pg.last"); return l && Date.now() - l.t < 3 * 3600e3 ? l.code : null; }, set: (code) => LS.set("ella.pg.last", { code, t: Date.now() }), clear: () => LS.set("ella.pg.last", null) };
const left = { has: (c) => (LS.get("ella.pg.left") || []).includes(c), add: (c) => LS.set("ella.pg.left", [...(LS.get("ella.pg.left") || []).filter((x) => x !== c), c].slice(-20)) };
export function resumeParam() { const c = lastRoom.get(); return c ? { code: c, quiet: true } : null; }

export function startParam() {
  const w = window.Telegram && window.Telegram.WebApp;
  const sp = (w && w.initDataUnsafe && w.initDataUnsafe.start_param) || new URLSearchParams(location.search).get("pg") || "";
  if (/^pg_[A-Z0-9]{5}$/.test(sp)) return { code: sp.slice(3), invite: true };
  return sp === "uno" || sp === "ludo" || sp === "chess" ? { game: sp } : null;
}

export function premiumEntry(ctx) {
  return h("button", { class: "pentry", onclick: () => openPremium(ctx) },
    h("span", { class: "crown" }, "👑"), h("span", {}, h("b", {}, "Premium Games"), h("small", {}, "UNO, Ludo & Chess with friends")), h("span", { class: "tag" }, "NEW"));
}

// ------------------------------------------------------------------ cards
function unoCard(c, extra = "") {
  const wild = c[0] === "w", col = wild ? "w" : c[0], g = GLYPH[wild ? c.slice(1) === "4" ? "4" : "" : c.slice(1)] ?? c.slice(1);
  return h("div", { class: `uc ${col} ${extra}`, dataset: { c } }, h("i", { class: "tl" }, g), h("span", { class: "ov" }, h("b", {}, g)), h("i", { class: "br" }, g));
}
const cardBack = (extra = "") => h("div", { class: "uc k " + extra }, h("span", { class: "ov" }, h("b", {}, "UNO")));

// ------------------------------------------------------------------ ludo geometry (15x15 grid, 10 units per cell => crisp vector)
const T = []; { const a = (c, r) => T.push([c, r]);
  for (let c = 1; c <= 5; c++) a(c, 6); for (let r = 5; r >= 0; r--) a(6, r); a(7, 0); a(8, 0);
  for (let r = 1; r <= 5; r++) a(8, r); for (let c = 9; c <= 14; c++) a(c, 6); a(14, 7); a(14, 8);
  for (let c = 13; c >= 9; c--) a(c, 8); for (let r = 9; r <= 14; r++) a(8, r); a(7, 14); a(6, 14);
  for (let r = 13; r >= 9; r--) a(6, r); for (let c = 5; c >= 0; c--) a(c, 8); a(0, 7); a(0, 6); }
const HOME = [[1, 2, 3, 4, 5].map((c) => [c, 7]), [1, 2, 3, 4, 5].map((r) => [7, r]), [13, 12, 11, 10, 9].map((c) => [c, 7]), [13, 12, 11, 10, 9].map((r) => [7, r])];
const OFF = [0, 13, 26, 39], SAFE = [0, 8, 13, 21, 26, 34, 39, 47], CORNER = [[0, 0], [9, 0], [9, 9], [0, 9]];
const cell = ([c, r]) => [c * 10 + 5, r * 10 + 5];
function tokenXY(seat, p, i) {
  if (p < 0) { const [bx, by] = CORNER[seat]; return [(bx + 2 + (i % 2) * 2) * 10 + 5, (by + 2 + (i >> 1) * 2) * 10 + 5]; }
  if (p <= 50) return cell(T[(OFF[seat] + p) % 52]);
  if (p <= 55) return cell(HOME[seat][p - 51]);
  const d = [[-9, 0], [0, -9], [9, 0], [0, 9]][seat];
  return [75 + d[0] + ((i % 2) - .5) * 4, 75 + d[1] + ((i >> 1) - .5) * 4];
}
function buildBoard() {
  const defs = S("defs");
  TOKEN_COLORS.forEach(([n, a, b]) => defs.append(S("radialGradient", { id: "t" + n, cx: ".35", cy: ".3", r: ".8" }, S("stop", { offset: "0", "stop-color": "#fff" }), S("stop", { offset: ".25", "stop-color": a }), S("stop", { offset: "1", "stop-color": b })),
    S("linearGradient", { id: "y" + n, x1: "0", y1: "0", x2: "1", y2: "1" }, S("stop", { offset: "0", "stop-color": a }), S("stop", { offset: "1", "stop-color": b }))));
  const svg = S("svg", { viewBox: "0 0 150 150", class: "board", role: "img", "aria-label": "Ludo board" }, defs,
    S("rect", { width: 150, height: 150, rx: 9, fill: "#fbf6ea", stroke: "#f4c76b", "stroke-width": 1.6 }));
  TOKEN_COLORS.forEach(([n], s) => {
    const [bx, by] = CORNER[s];
    svg.append(S("rect", { x: bx * 10, y: by * 10, width: 60, height: 60, fill: `url(#y${n})`, rx: s === 0 ? 9 : 0 }), S("rect", { x: bx * 10 + 10, y: by * 10 + 10, width: 40, height: 40, rx: 8, fill: "#fff", opacity: ".93" }));
    [0, 1, 2, 3].forEach((i) => { const [x, y] = tokenXY(s, -1, i); svg.append(S("circle", { cx: x, cy: y, r: 5.4, fill: `url(#y${n})`, opacity: ".35" })); });
  });
  T.forEach(([c, r], i) => { const st = OFF.indexOf(i); svg.append(S("rect", { x: c * 10 + .4, y: r * 10 + .4, width: 9.2, height: 9.2, rx: 1.6, fill: st >= 0 ? `url(#y${TOKEN_COLORS[st][0]})` : "#fff", stroke: "#cfc6b0", "stroke-width": .5 }));
    if (SAFE.includes(i) && st < 0) svg.append(S("text", { x: c * 10 + 5, y: r * 10 + 7.3, "text-anchor": "middle", "font-size": 7, fill: "#b7a98a" }, "★")); });
  HOME.forEach((col, s) => col.forEach(([c, r]) => svg.append(S("rect", { x: c * 10 + .4, y: r * 10 + .4, width: 9.2, height: 9.2, rx: 1.6, fill: `url(#y${TOKEN_COLORS[s][0]})`, opacity: ".9" }))));
  [[[60, 60], [75, 75], [60, 90]], [[60, 60], [90, 60], [75, 75]], [[90, 60], [90, 90], [75, 75]], [[60, 90], [90, 90], [75, 75]]].forEach((pts, s) => svg.append(S("polygon", { points: pts.map((p) => p.join(",")).join(" "), fill: `url(#y${TOKEN_COLORS[s][0]})`, stroke: "#fff", "stroke-width": .6 })));
  return svg;
}

// ------------------------------------------------------------------ overlay + flow
export function openPremium(ctx, start) {
  const ov = h("div", { class: "pg" }), inner = h("div", { class: "pg-in" });
  ov.append(inner);
  themeAttrs(ov);
  document.querySelector("#overlay-root").append(ov);
  let room = null, gen = 0, ctl = null, timer = 0, board = null, cSel = null, hold = false, pend = null, rolling = false, recvAt = 0;
  const ticks = [], put = (...k) => inner.replaceChildren(...k.filter(Boolean));
  const toast = ctx.toast, errText = (e) => (e && e.message) || "Something went wrong.";
  const back = () => { if (room) leaveView(); else close(); };
  function close() { gen++; ctl && ctl.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", wake); window.removeEventListener("online", wake); ov.remove(); ctx.popLayer(back); tg.playing(false); }
  ctx.pushLayer(back);
  const wake = async () => { if (document.hidden || !room) return; const code = room.code; try { const d = await call("GET", `/api/premium/rooms/${code}`); if (room && room.code === code) { apply(d); watch(); } } catch (e) { if (e.status === 404) { lastRoom.clear(); toast("This table was closed."); leaveView(); } } };
  document.addEventListener("visibilitychange", wake); window.addEventListener("online", wake);
  const hdr = (title, sub, onBack) => h("div", { class: "pg-top" }, h("button", { class: "ibtn", "aria-label": "Back", onclick: onBack || back }, icon("back")), h("h1", {}, title, sub ? h("small", {}, sub) : null),
    h("button", { class: "ibtn", "aria-label": "Look and sound settings", onclick: () => settingsSheet(ov, () => themeAttrs(ov)) }, "⚙\uFE0E"));

  function leaveView() { gen++; ctl && ctl.abort(); clearInterval(timer); lastRoom.clear(); room = null; board = null; tg.playing(false); hub(); }

  async function hub() {
    put(hdr("Trending", "Play live with friends", close), h("div", { id: "mine" }),
      h("div", { class: "tiles" }, tile("uno", "House rules, UNO call, jump-in. Play friends or the computer.", fanArt()), tile("ludo", "Race four tokens home. Solo, teams or vs computer.", ludoArt()), tile("chess", "Full rules, clocks, takebacks. Duel a friend or the computer.", chessArt())),
      h("h3", { class: "sub" }, "Have an invite code?"),
      (() => { const inp = h("input", { maxlength: "5", placeholder: "CODE", "aria-label": "Table code", autocapitalize: "characters" });
        return h("div", { class: "joinrow" }, inp, h("button", { class: "pbtn", onclick: () => enter(inp.value.trim().toUpperCase()) }, "Join")); })());
    try {
      const { rooms } = await call("GET", "/api/premium/mine"); if (room) return;
      const box = inner.querySelector("#mine"); if (!box || !rooms.length) return;
      box.append(h("h3", { class: "sub first" }, "Continue playing"), ...rooms.map((r) => h("button", { class: "resume " + r.game, onclick: () => enter(r.code) },
        h("span", { class: "dot " + (r.status === "playing" ? "on" : "") }), h("span", { class: "rt" }, h("b", {}, NAMES[r.game], r.group ? " · " + r.group : ""), h("small", {}, r.status === "playing" ? "Game in progress" : `Waiting for players (${r.players})`)), h("span", { class: "go2" }, "Resume"))));
    } catch {}
  }
  function tile(game, text, art) {
    return h("div", { class: "tile " + game }, art, h("h2", {}, NAMES[game]), h("p", {}, text),
      h("button", { class: "pbtn go", onclick: async (e) => { e.currentTarget.disabled = true; try { apply(await call("POST", "/api/premium/rooms", { game })); watch(); } catch (x) { toast(errText(x)); e.target.disabled = false; } } }, "Create table"));
  }
  function fanArt() { const f = h("div", { class: "fan" }); ["r5", "yd", "gs", "w4"].forEach((c, i) => { const el = unoCard(c); el.style.transform = `rotate(${(i - 1.5) * 16}deg) translateX(${(i - 1.5) * 14}px)`; f.append(el); }); return f; }
  function chessArt() { const s = S("svg", { viewBox: "0 0 40 40", class: "art" }); for (let i = 0; i < 16; i++) s.append(S("rect", { x: (i % 4) * 10, y: (i >> 2) * 10, width: 10, height: 10, fill: (i + (i >> 2)) % 2 ? "#5a3d2e" : "#ecdcb8" }));
    s.append(S("text", { x: 15, y: 26, "font-size": 20, class: "gl wp" }, "♞\uFE0E"), S("text", { x: 25, y: 16, "font-size": 12, class: "gl bp" }, "♛\uFE0E")); return s; }
  function ludoArt() { const s = buildBoard(); s.setAttribute("class", "art"); return s; }

  async function enter(code, o = {}) {
    if (!/^[A-Z0-9]{5}$/.test(code)) return toast("Enter the 5-character table code.");
    try {
      let d = await call("GET", `/api/premium/rooms/${code}`), mineNow = d.players.some((p) => p.id === d.me);
      if (o.quiet && (d.status === "done" || !mineNow)) { lastRoom.clear(); return room ? null : o.fromBoot ? close() : hub(); }          // refresh: only resume a live table you are in
      if (!mineNow && d.status === "lobby") { if (o.invite && left.has(code)) return hub(); d = await call("POST", `/api/premium/rooms/${code}/act`, { a: "join" }); }
      apply(d); watch();
    } catch (e) { if (o.quiet) { lastRoom.clear(); return room ? null : o.fromBoot ? close() : hub(); } toast(errText(e)); if (!room) hub(); }
  }
  async function act(a, extra = {}) { try { const code = room.code; if (a === "leave") { left.add(code); lastRoom.clear(); } const d = await call("POST", `/api/premium/rooms/${code}/act`, { a, ...extra }); if (d && d.left) return; apply(d); } catch (e) { toast(errText(e)); } }
  async function watch() {
    const my = ++gen; ctl && ctl.abort(); ctl = new AbortController(); const code = room.code;
    let fails = 0;
    while (gen === my) {
      try { const d = await call("GET", `/api/premium/rooms/${code}?v=${room.v}&wait=1`, undefined, { signal: ctl.signal }); if (gen !== my) return; fails = 0; if (!d.same) apply(d); }
      catch (e) { if (gen !== my || e.name === "AbortError") return; if (e.status === 404) { lastRoom.clear(); toast("This table was closed."); return leaveView(); } if (++fails === 3) toast("Reconnecting…"); await sleep(Math.min(1500 * fails, 5000)); }
    }
  }
  function apply(d) {
    if (hold) { if (!pend || d.v > pend.v) pend = d; return; }
    if (room && d.v <= room.v && d.code === room.code) return;
    const prev = room, first = !room || room.code !== d.code; cSel = null; room = d; recvAt = Date.now(); if (d.players.some((p) => p.id === d.me)) lastRoom.set(d.code); if (first) board = null; render(); if (!first) fx(prev, d);
  }
  function fx(p, d) {
    if (!p || !p.g || !d.g) return;
    if (d.status === "done" && p.status !== "done") { const w = won(d); w ? sfx.win() : sfx.lose(); notify(w ? "success" : "error"); return; }
    if (d.game === "chess") { if (d.g.mv.length > p.g.mv.length) { const cap = (d.g.lost.w + d.g.lost.b).length > (p.g.lost.w + p.g.lost.b).length; cap ? sfx.capture() : sfx.move(); buzz(cap ? "medium" : "light"); } }
    else if (d.game === "uno") { if (d.g.top !== p.g.top) sfx.card(); if (d.g.vuln && d.g.vuln !== p.g.vuln && d.g.vuln !== d.me) { sfx.alert(); buzz("heavy"); } }
    else if (d.g.rv && (d.g.last !== p.g.last)) sfx.move();
    if (d.status === "playing" && d.g.turn === d.me && p.g.turn !== d.me) { sfx.turn(); notify("warning"); }
  }
  const won = (d) => d.winner === d.me || (d.game === "ludo" && d.g && d.g.team && d.g.wt != null && d.g.wt === d.g.seat[d.me] % 2);
  // ---- screens
  function render() {
    const d = room, me = d.me, inRoom = d.players.some((p) => p.id === me);
    tg.playing(d.status === "playing");
    clearInterval(timer); ticks.length = 0;
    ov.querySelectorAll(".sheet.end").forEach((e) => e.remove());
    if (d.status === "lobby" || !d.g) return lobbyView(d, inRoom);
    ({ uno: unoView, ludo: ludoView, chess: chessView })[d.game](d);
    timer = setInterval(() => ticks.forEach((f) => f()), 500);
    if (d.status === "done" || (d.game === "uno" && d.g.rdone)) winnerView(d);
  }
  function invite(d) {
    const url = d.link || "", text = `Join my ${NAMES[d.game]} table on Ella! Code ${d.code}`, w = window.Telegram && window.Telegram.WebApp;
    if (url && w && w.openTelegramLink) return w.openTelegramLink("https://t.me/share/url?" + new URLSearchParams({ url, text }));
    (navigator.clipboard ? navigator.clipboard.writeText(url || d.code) : Promise.reject()).then(() => toast("Invite link copied"), () => toast("Code: " + d.code));
  }
  function lobbyView(d, inRoom) {
    const isHost = d.host === d.me, seats = [], bots = d.players.filter((p) => p.bot).length;
    for (let i = 0; i < d.max; i++) { const p = d.players[i]; seats.push(p ? h("div", { class: "seat" }, p.bot ? h("div", { class: "avatar bot" }, "🤖") : ctx.avatar({ display_name: p.name, photo_url: p.photo }), h("span", {}, h("b", {}, p.name), p.id === d.host ? h("small", {}, "👑 Host") : p.bot ? h("small", {}, "Computer") : h("small", {}, "Ready ✓"))) : h("div", { class: "seat empty" }, "Waiting for player…")); }
    put(hdr(NAMES[d.game], d.group ? "Group table · " + d.group : "Private table"),
      h("div", {}, h("span", { class: "code" }, d.code)),
      h("div", { class: "seats" }, seats),
      isHost ? h("div", { class: "acts two botrow" },
        h("button", { class: "pbtn ghost", disabled: d.players.length >= d.max, onclick: () => act("addbot") }, "🤖 Add computer"),
        h("button", { class: "pbtn ghost", disabled: !bots, onclick: () => act("rmbot") }, "Remove computer")) : null,
      rules(d, isHost, bots),
      inRoom ? h("div", { class: "acts" },
        h("button", { class: "pbtn full", onclick: () => invite(d) }, "📤 Invite friends"),
        isHost ? h("button", { class: "pbtn full", disabled: d.players.length < d.min, onclick: () => act("start") }, d.players.length < d.min ? `Need ${d.min - d.players.length} more to start` : "Start game") : h("p", { class: "hint" }, "Waiting for the host to start…"),
        h("button", { class: "pbtn ghost full", onclick: async () => { await act("leave"); leaveView(); } }, "Leave table"))
        : h("button", { class: "pbtn full", onclick: () => act("join") }, "Join table"));
  }
  const PTS = [[0, "Single round"], [100, "To 100"], [200, "To 200"], [500, "To 500"]], TCS = [["off", "No clock"], ["1+0", "1 min"], ["3+2", "3+2"], ["5+0", "5 min"], ["10+0", "10 min"]];
  function rules(d, isHost, bots) {
    const tog = (k, label) => h("button", { class: "rule", role: "switch", "aria-checked": String(!!d.opts[k]), disabled: !isHost, onclick: () => act("opt", { k, v: !d.opts[k] }) }, h("span", {}, label), h("i", {}));
    const seg = (k, label, list) => h("div", { class: "rule col" }, h("span", {}, label), h("div", { class: "seg" }, list.map(([v, t]) => h("button", { "aria-pressed": String(d.opts[k] === v), disabled: !isHost, onclick: () => act("opt", { k, v }) }, t))));
    const R = d.game === "uno" ? [tog("stack", "Stack +2 / +4"), tog("callout", "UNO call (get caught = +2)"), tog("seven", "7-0: swap & rotate hands"), tog("jump", "Jump-in (same card, any turn)"), seg("pts", "Match", PTS)]
      : d.game === "ludo" ? [tog("block", "Blockade (2 tokens = wall)"), tog("team", "Team mode (4 players: 1+3 vs 2+4)")]
      : [seg("tc", "Clock", TCS), bots ? seg("lvl", "Computer level", [["easy", "Easy"], ["normal", "Normal"]]) : null];
    return h("div", { class: "rules" }, R);
  }
  function turnBar(d, name, mine) {
    const left = h("small", {}); const bar = h("div", { class: "turnbar" + (mine ? " me" : "") }, mine ? "Your turn " : name + "'s turn ", left);
    const t = () => { const s = Math.max(0, d.deadline - Math.floor(Date.now() / 1000)); left.textContent = d.status === "playing" && !(d.game === "chess" && d.g.tc) && !(by(d, d.g.turn).bot) ? `· ${s}s` : ""; };
    t(); ticks.push(t); return bar;
  }
  const by = (d, id) => d.players.find((p) => p.id === id) || { name: "Player" };

  function unoView(d) {
    const g = d.g, mine = g.turn === d.me && d.status === "playing" && !g.rdone, can = new Set(g.rdone ? [] : g.playable), vuln = g.vuln;
    const opps = d.players.filter((p) => p.id !== d.me).map((p) => h("div", { class: "opp" + (g.turn === p.id && d.status === "playing" ? " on-turn" : "") }, p.bot ? h("div", { class: "avatar bot" }, "🤖") : ctx.avatar({ display_name: p.name, photo_url: p.photo }), h("span", {}, p.name), h("span", { class: "cnt" + (g.counts[p.id] === 1 ? " one" : "") }, g.counts[p.id] === 1 ? "UNO!" : g.counts[p.id] + " cards")));
    const others = d.players.filter((p) => p.id !== d.me);
    const pick = (c) => {
      if (!can.has(c)) return buzz("light");
      const seven = d.opts.seven && c.slice(1) === "7" && g.hand.length > 1, go = (extra) => act("play", { card: c, ...extra });
      if (c[0] === "w") return colorSheet((color) => go({ color }));
      if (seven) return playerSheet(others, (to) => go({ to }));
      go({});
    };
    const hand = h("div", { class: "hand" }, g.hand.map((c) => { const el = unoCard(c, can.has(c) ? "can" : "dim"); el.addEventListener("click", () => pick(c)); return el; }));
    const draw = cardBack("pile" + (mine && !g.drawn ? " tap" : "")); draw.classList.add("pile"); draw.addEventListener("click", () => mine && !g.drawn && act("draw"));
    const disc = unoCard(g.top, "disc");
    const score = g.target ? h("div", { class: "plrs score" }, d.players.map((p) => h("div", { class: "me-chip" }, p.name, h("b", {}, " " + (g.score[p.id] || 0)), h("small", {}, " /" + g.target)))) : null;
    const callBtn = vuln === d.me ? h("button", { class: "pbtn uno-call", onclick: () => act("uno") }, "UNO!") : vuln && d.status === "playing" ? h("button", { class: "pbtn catch", onclick: () => act("catch") }, "🚨 Catch " + by(d, vuln).name + "!") : null;
    put(hdr("UNO", "Table " + d.code + (g.target ? " · Round " + g.round : "")),
      score,
      h("div", { class: "felt" }, h("div", { class: "opps" }, opps), h("div", { class: "mid" }, h("span", { class: "ring " + g.color }), draw, disc), turnBar(d, by(d, g.turn).name, mine),
        g.last ? h("div", { class: "hint tight" }, g.last) : null,
        g.pend ? h("div", { class: "turnbar" }, mine ? `Stack a draw card, or take ${g.pend}` : `+${g.pend} pending`) : null,
        callBtn,
        !mine && can.size && d.status === "playing" ? h("div", { class: "hint tight" }, "⚡ Jump in with your matching card!") : null,
        g.drawn && mine ? h("div", { class: "acts two" }, h("button", { class: "pbtn", onclick: () => pick(g.drawn) }, "Play drawn card"), h("button", { class: "pbtn ghost", onclick: () => act("pass") }, "Keep & pass")) : null, hand));
  }
  function playerSheet(list, done) {
    const sh = h("div", { class: "sheet", onclick: (e) => e.target === sh && sh.remove() }, h("div", { class: "box" }, h("h3", {}, "Swap hands with…"),
      h("div", { class: "acts" }, list.map((p) => h("button", { class: "pbtn ghost full", onclick: () => { sh.remove(); done(p.id); } }, p.name + " · " + (room.g.counts[p.id]) + " cards")))));
    ov.append(sh);
  }
  function colorSheet(done) {
    const sh = h("div", { class: "sheet", onclick: (e) => e.target === sh && sh.remove() }, h("div", { class: "box" }, h("h3", {}, "Choose a colour"),
      h("div", { class: "cols" }, ["r", "y", "g", "b"].map((c) => h("button", { class: c, "aria-label": c, onclick: () => { sh.remove(); done(c); } })))));
    ov.append(sh);
  }

  const FACES = [[], [4], [0, 8], [0, 4, 8], [0, 2, 6, 8], [0, 2, 4, 6, 8], [0, 2, 3, 5, 6, 8]];
  const setFace = (dice, n) => [...dice.children].forEach((b, i) => b.classList.toggle("on", FACES[n].includes(i)));
  async function rollDice(dice) {
    if (rolling) return; rolling = hold = true; pend = null; dice.classList.remove("can"); dice.classList.add("rolling"); sfx.dice(); buzz("medium");
    const iv = setInterval(() => setFace(dice, 1 + Math.floor(Math.random() * 6)), 90), t0 = Date.now(); let res = null;
    try { res = await call("POST", `/api/premium/rooms/${room.code}/act`, { a: "roll" }); } catch (e) { toast(errText(e)); }
    await sleep(Math.max(0, 700 - (Date.now() - t0))); clearInterval(iv); hold = rolling = false;
    const d = [pend, res].filter(Boolean).sort((a, b) => b.v - a.v)[0]; pend = null; if (d) apply(d); else render();
  }
  function ludoView(d) {
    const g = d.g, mine = g.turn === d.me && d.status === "playing";
    if (!board) { board = { svg: buildBoard(), toks: new Map() }; }
    const layer = board.svg;
    d.players.forEach((p) => { const seat = g.seat[p.id]; (g.tok[p.id] || []).forEach((pos, i) => {
      const key = p.id + ":" + i; let t = board.toks.get(key);
      if (!t) { t = S("g", { class: "tk" }, S("circle", { r: 7.2, fill: "none", class: "ring-p" }), S("circle", { cy: 1.2, r: 5.2, fill: "#000", opacity: ".3" }), S("circle", { r: 5, fill: `url(#t${TOKEN_COLORS[seat][0]})`, stroke: "#fff", "stroke-width": 1 }), S("circle", { cx: -1.6, cy: -1.8, r: 1.3, fill: "#fff", opacity: ".7" })); board.toks.set(key, t); layer.append(t); }
      let n = 0; d.players.forEach((q) => (g.tok[q.id] || []).forEach((o, j) => { if (o === pos && pos >= 0 && pos <= 50 && (OFF[g.seat[q.id]] + o) % 52 === (OFF[seat] + pos) % 52 && (q.id < p.id || (q.id === p.id && j < i))) n++; }));
      const [x, y] = tokenXY(seat, pos, i); t.style.transform = `translate(${x + n * 2.2}px,${y - n * 2.2}px)`;
      const movable = mine && d.status === "playing" && g.movable.includes(i) && p.id === d.me;
      t.classList.toggle("can", movable); t.onclick = movable ? () => { sfx.move(); buzz("light"); act("move", { token: i }); } : null; }); });
    [...board.toks.values()].forEach((t) => layer.append(t));
    const dice = h("button", { class: "dice" + (mine && !g.roll ? " can" : ""), "aria-label": "Dice", onclick: () => { if (mine && !g.roll) rollDice(dice); } }, Array.from({ length: 9 }, () => h("b", {})));
    setFace(dice, g.roll || g.rv || 0);
    const chips = d.players.map((p) => h("div", { class: "me-chip" + (g.turn === p.id && d.status === "playing" ? " on-turn" : "") }, h("i", { class: "dot", "data-c": "" }), p.bot ? h("div", { class: "avatar bot" }, "🤖") : ctx.avatar({ display_name: p.name, photo_url: p.photo }), p.name + (g.team ? " · Team " + (g.seat[p.id] % 2 ? "B" : "A") : "")));
    chips.forEach((c, i) => c.querySelector("i").style.background = TOKEN_COLORS[g.seat[d.players[i].id]][2]);
    const who = g.turn === d.me ? "You get" : by(d, g.turn).name + " gets", EX = { six: "a six - another roll!", capture: "a capture - another roll!", home: "a token home - another roll!" };
    put(hdr("LUDO", "Table " + d.code + (g.team ? " · Teams" : "")), h("div", { class: "plrs" }, chips), board.svg,
      g.extra && d.status === "playing" ? h("div", { class: "bonus" }, "🎉 " + who + " " + EX[g.extra]) : null, dice, turnBar(d, by(d, g.turn).name, mine),
      h("p", { class: "hint" }, mine && g.roll ? "Tap a glowing token to move." : g.last || "Roll a 6 to bring a token out."));
  }

  const mmss = (n) => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
  function chessView(d) {
    const g = d.g, flip = g.w !== d.me, mine = g.turn === d.me && d.status === "playing", rows = g.b, mySide = g.w === d.me, live = d.status === "playing";
    const dests = (r, c) => g.legal.filter((m) => m[0] === r && m[1] === c).map((m) => [m[2], m[3]]);
    const go = (ar, ac) => {
      if (!mine) return;
      const p = rows[ar][ac];
      if (cSel && dests(...cSel).some((x) => x[0] === ar && x[1] === ac)) {
        const from = cSel, pc = rows[from[0]][from[1]], send = (promo) => { cSel = null; act("move", { m: [...from, ar, ac], promo }); };
        return pc.toLowerCase() === "p" && (ar === 0 || ar === 7) ? promoSheet(mySide, send) : send("Q");
      }
      cSel = p !== "." && (p === p.toUpperCase()) === mySide && dests(ar, ac).length ? [ar, ac] : null; buzz("light"); render();
    };
    const svg = S("svg", { viewBox: "-4 -4 88 88", class: "board cb", role: "img", "aria-label": "Chess board" },
      S("defs", {}, S("linearGradient", { id: "cf", x1: "0", y1: "0", x2: "1", y2: "1" }, S("stop", { offset: "0", "stop-color": "#ffe3a0" }), S("stop", { offset: "1", "stop-color": "#b98a2e" })),
        S("radialGradient", { id: "ck" }, S("stop", { offset: "0", "stop-color": "#ff3b45", "stop-opacity": ".95" }), S("stop", { offset: "1", "stop-color": "#ff3b45", "stop-opacity": "0" }))),
      S("rect", { x: -4, y: -4, width: 88, height: 88, rx: 5, fill: "url(#cf)" }), S("rect", { x: -1, y: -1, width: 82, height: 82, rx: 2, fill: "#2a1b12" }));
    const to = cSel ? dests(...cSel) : [];
    for (let dr = 0; dr < 8; dr++) for (let dc = 0; dc < 8; dc++) {
      const ar = flip ? 7 - dr : dr, ac = flip ? 7 - dc : dc, p = rows[ar][ac], x = dc * 10, y = dr * 10;
      svg.append(S("rect", { x, y, width: 10, height: 10, class: "sq " + ((dr + dc) % 2 ? "d" : "l") }));
      if (g.last && ((g.last[0] === ar && g.last[1] === ac) || (g.last[2] === ar && g.last[3] === ac))) svg.append(S("rect", { x, y, width: 10, height: 10, fill: "#f4c76b", opacity: ".42" }));
      if (cSel && cSel[0] === ar && cSel[1] === ac) svg.append(S("rect", { x, y, width: 10, height: 10, fill: "#6fe3b0", opacity: ".55" }));
      if (g.check && p.toLowerCase() === "k" && (p === p.toUpperCase()) === (g.turn === g.w)) svg.append(S("circle", { cx: x + 5, cy: y + 5, r: 7, fill: "url(#ck)" }));
      if (p !== ".") svg.append(S("text", { x: x + 5, y: y + 7.7, "text-anchor": "middle", "font-size": 8.6, class: "gl " + (p === p.toUpperCase() ? "wp" : "bp") }, PIECE[p.toLowerCase()] + "\uFE0E"));
      if (to.some((t) => t[0] === ar && t[1] === ac)) svg.append(p === "." ? S("circle", { cx: x + 5, cy: y + 5, r: 1.9, fill: "#6fe3b0", opacity: ".9" }) : S("circle", { cx: x + 5, cy: y + 5, r: 4.4, fill: "none", stroke: "#6fe3b0", "stroke-width": 1, opacity: ".95" }));
      if (dc === 0) svg.append(S("text", { x: -2.1, y: y + 6, "text-anchor": "middle", "font-size": 2.8, class: "coord" }, String(8 - ar)));
      if (dr === 7) svg.append(S("text", { x: x + 5, y: 83, "text-anchor": "middle", "font-size": 2.8, class: "coord" }, "abcdefgh"[ac]));
      const hit = S("rect", { x, y, width: 10, height: 10, fill: "transparent", class: "hit" }); hit.addEventListener("click", () => go(ar, ac)); svg.append(hit);
    }
    const strip = (p) => {
      const white = g.w === p.id, got = white ? g.lost.b : g.lost.w;                 // what this player has captured
      const clock = h("span", { class: "clk" });
      const upd = () => { let n = g.clk[p.id] ?? 0; if (g.turn === p.id && live) n = Math.max(0, g.rem - Math.floor((Date.now() - recvAt) / 1000)); clock.textContent = g.tc ? mmss(n) : ""; clock.classList.toggle("low", g.tc && live && n <= 10); };
      upd(); ticks.push(upd);
      return h("div", { class: "pstrip" + (g.turn === p.id && live ? " on-turn" : "") }, p.bot ? h("div", { class: "avatar bot" }, "🤖") : ctx.avatar({ display_name: p.name, photo_url: p.photo }),
        h("span", { class: "pn" }, h("b", {}, p.name), h("small", { class: white ? "capw" : "capb" }, [...got].map((k) => CAP[k]).join(""))), clock);
    };
    const opp = d.players.find((p) => p.id !== d.me) || d.players[0], me = d.players.find((p) => p.id === d.me) || d.players[0];
    const mv = h("div", { class: "moves", "aria-label": "Move history" }, g.mv.map((m, i) => h("span", { class: i % 2 ? "bk" : "wh" }, i % 2 ? m : `${i / 2 + 1}. ${m}`)));
    setTimeout(() => { mv.scrollLeft = mv.scrollWidth; }, 0);
    const oppId = opp.id, oppBot = !!opp.bot;
    const offer = g.draw && g.draw !== d.me ? h("div", { class: "offer" }, h("span", {}, "🤝 Opponent offers a draw"), h("button", { class: "pbtn", onclick: () => act("draw") }, "Accept"), h("button", { class: "pbtn ghost", onclick: () => act("draw_no") }, "Decline"))
      : g.undo && g.undo !== d.me ? h("div", { class: "offer" }, h("span", {}, "↩️ Opponent asks to take back a move"), h("button", { class: "pbtn", onclick: () => act("undo") }, "Allow"), h("button", { class: "pbtn ghost", onclick: () => act("undo_no") }, "No")) : null;
    const tools = live ? h("div", { class: "acts three" },
      g.draw === d.me ? h("button", { class: "pbtn ghost", onclick: () => act("draw_no") }, "Cancel draw offer") : h("button", { class: "pbtn ghost", onclick: () => act("draw") }, "🤝 Draw"),
      g.undo === d.me ? h("button", { class: "pbtn ghost", onclick: () => act("undo_no") }, "Cancel undo") : h("button", { class: "pbtn ghost", disabled: !g.can_undo, onclick: () => act("undo") }, "↩️ Undo"),
      h("button", { class: "pbtn ghost", onclick: () => confirmSheet("Resign this game?", "Resign", () => act("resign")) }, "🏳 Resign")) : null;
    put(hdr("CHESS", "Table " + d.code + (oppBot ? " · vs computer" : "")), strip(opp), svg, strip(me), mv, offer,
      h("p", { class: "hint tight" }, g.check && live ? "Check!" : mine ? "Tap a piece, then a highlighted square." : live ? "Waiting for your opponent…" : ""), tools);
  }
  function promoSheet(white, done) {
    const sh = h("div", { class: "sheet", onclick: (e) => e.target === sh && sh.remove() }, h("div", { class: "box" }, h("h3", {}, "Promote to"),
      h("div", { class: "cols" }, ["Q", "R", "B", "N"].map((k) => h("button", { class: "pr", "aria-label": k, onclick: () => { sh.remove(); done(k); } }, h("span", { class: "prg " + (white ? "wp" : "bp") }, PIECE[k.toLowerCase()] + "\uFE0E"))))));
    ov.append(sh);
  }
  function confirmSheet(text, label, yes) {
    const sh = h("div", { class: "sheet" }, h("div", { class: "box" }, h("h3", {}, text), h("div", { class: "acts two", }, h("button", { class: "pbtn ghost", onclick: () => sh.remove() }, "Cancel"), h("button", { class: "pbtn", onclick: () => { sh.remove(); yes(); } }, label))));
    ov.append(sh);
  }

  function winnerView(d) {
    const rd = d.game === "uno" && d.g.rdone && d.status === "playing";
    if (rd) {
      const sh = h("div", { class: "sheet end" }, h("div", { class: "box" }, h("div", { class: "win" }, "🏁"),
        h("div", { class: "win-t" }, (d.g.rdone.w === d.me ? "You" : by(d, d.g.rdone.w).name) + " won the round"), h("p", { class: "hint" }, `+${d.g.rdone.pts} points`),
        h("div", { class: "tbl" }, d.players.map((p) => h("div", {}, h("span", {}, p.name), h("b", {}, `${d.g.score[p.id] || 0} / ${d.g.target}`)))),
        h("button", { class: "pbtn full", onclick: () => { sh.remove(); act("next"); } }, "Next round")));
      return ov.append(sh);
    }
    const w = won(d), draw = d.winner == null, sh = h("div", { class: "sheet end" }, h("div", { class: "box" }, h("div", { class: "win" }, draw ? "🤝" : w ? "🏆" : "🎉"),
      h("div", { class: "win-t" }, draw ? "Draw" : d.game === "ludo" && d.g.team ? (w ? "Your team won!" : "Other team won") : w ? "You won!" : by(d, d.winner).name + " won"), d.g && d.g.res ? h("p", { class: "hint" }, d.g.res) : null,
      d.game === "uno" && d.g.target ? h("div", { class: "tbl" }, d.players.map((p) => h("div", {}, h("span", {}, p.name), h("b", {}, String(d.g.score[p.id] || 0))))) : null,
      d.host === d.me ? h("button", { class: "pbtn full", onclick: () => { sh.remove(); act("again"); } }, "Play again") : h("p", { class: "hint" }, "Waiting for the host…"),
      h("button", { class: "pbtn ghost full", onclick: async () => { sh.remove(); await act("leave"); leaveView(); } }, "Leave table")));
    ov.append(sh);
  }

  if (start && start.code) enter(start.code, { quiet: !!start.quiet, invite: !!start.invite, fromBoot: !!start.quiet }); else hub();
}
