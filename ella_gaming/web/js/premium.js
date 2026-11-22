// Premium Games (UNO + LUDO) for Ella Gaming. Self-contained: only uses the existing login/api helpers.
import { h, icon } from "./dom.js";
import { tg } from "./tg.js";
import { call } from "./api.js";

const NS = "http://www.w3.org/2000/svg";
const S = (t, a = {}, ...k) => { const e = document.createElementNS(NS, t); for (const [n, v] of Object.entries(a)) e.setAttribute(n, v); k.flat().forEach((x) => x && e.append(x)); return e; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NAMES = { uno: "UNO", ludo: "LUDO", chess: "CHESS" };
const PIECE = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
const GLYPH = { s: "⊘", r: "⇄", d: "+2", "4": "+4", "": "" };
const TOKEN_COLORS = [["red", "#ff6b6b", "#c4161f"], ["green", "#46dd86", "#0f8a47"], ["yellow", "#ffe066", "#e0a400"], ["blue", "#5b98ff", "#1743b8"]];

export function startParam() {
  const w = window.Telegram && window.Telegram.WebApp;
  const sp = (w && w.initDataUnsafe && w.initDataUnsafe.start_param) || new URLSearchParams(location.search).get("pg") || "";
  if (/^pg_[A-Z0-9]{5}$/.test(sp)) return { code: sp.slice(3) };
  return sp === "uno" || sp === "ludo" || sp === "chess" ? { game: sp } : null;
}

export function premiumEntry(ctx) {
  return h("button", { class: "pentry", onclick: () => openPremium(ctx) },
    h("span", { class: "crown" }, "👑"), h("span", {}, h("b", {}, "Premium Games"), h("small", {}, "UNO & LUDO with friends, live")), h("span", { class: "tag" }, "NEW"));
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
  document.querySelector("#overlay-root").append(ov);
  let room = null, gen = 0, ctl = null, timer = 0, board = null, cSel = null;
  const toast = ctx.toast, errText = (e) => (e && e.message) || "Something went wrong.";
  const back = () => { if (room) leaveView(); else close(); };
  function close() { gen++; ctl && ctl.abort(); clearInterval(timer); ov.remove(); ctx.popLayer(back); tg.playing(false); }
  ctx.pushLayer(back);
  const hdr = (title, sub, onBack) => h("div", { class: "pg-top" }, h("button", { class: "ibtn", "aria-label": "Back", onclick: onBack || back }, icon("back")), h("h1", {}, title, sub ? h("small", {}, sub) : null));

  function leaveView() { gen++; ctl && ctl.abort(); clearInterval(timer); room = null; board = null; tg.playing(false); hub(); }

  async function hub() {
    inner.replaceChildren(hdr("Premium Games", "Play live with friends", close),
      h("div", { class: "tiles" }, tile("uno", "Match colours, stack the pressure, shout UNO.", fanArt()), tile("ludo", "Race four tokens home. Capture rivals.", ludoArt()), tile("chess", "The classic duel. Full rules, checkmate wins.", chessArt())),
      h("h3", { class: "sub" }, "Have an invite code?"),
      (() => { const inp = h("input", { maxlength: "5", placeholder: "CODE", "aria-label": "Table code", autocapitalize: "characters" });
        return h("div", { class: "joinrow" }, inp, h("button", { class: "pbtn", onclick: () => enter(inp.value.trim().toUpperCase()) }, "Join")); })(),
      h("div", { id: "mine" }));
    try {
      const { rooms } = await call("GET", "/api/premium/mine"); if (room) return;
      const box = inner.querySelector("#mine"); if (!box || !rooms.length) return;
      box.append(h("h3", { class: "sub" }, "Your tables"), ...rooms.map((r) => h("button", { class: "mine", onclick: () => enter(r.code) },
        h("span", { class: "dot " + (r.status === "playing" ? "on" : "") }), h("b", {}, NAMES[r.game], r.group ? " · " + r.group : ""), h("small", {}, r.status === "playing" ? "In progress" : `Waiting ${r.players}`))));
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

  async function enter(code) {
    if (!/^[A-Z0-9]{5}$/.test(code)) return toast("Enter the 5-character table code.");
    try { let d = await call("GET", `/api/premium/rooms/${code}`); if (!d.players.some((p) => p.id === d.me) && d.status === "lobby") d = await call("POST", `/api/premium/rooms/${code}/act`, { a: "join" }); apply(d); watch(); }
    catch (e) { toast(errText(e)); if (!room) hub(); }
  }
  async function act(a, extra = {}) { try { apply(await call("POST", `/api/premium/rooms/${room.code}/act`, { a, ...extra })); } catch (e) { toast(errText(e)); } }
  async function watch() {
    const my = ++gen; ctl && ctl.abort(); ctl = new AbortController(); const code = room.code;
    while (gen === my) {
      try { const d = await call("GET", `/api/premium/rooms/${code}?v=${room.v}&wait=1`, undefined, { signal: ctl.signal }); if (gen !== my) return; if (!d.same) apply(d); }
      catch (e) { if (gen !== my || e.name === "AbortError") return; if (e.status === 404) { toast("This table was closed."); return leaveView(); } await sleep(1500); }
    }
  }
  function apply(d) { if (room && d.v < room.v && d.code === room.code) return; const first = !room || room.code !== d.code; cSel = null; room = d; if (first) board = null; render(); }

  // ---- screens
  function render() {
    const d = room, me = d.me, inRoom = d.players.some((p) => p.id === me);
    tg.playing(d.status === "playing");
    clearInterval(timer);
    if (d.status === "lobby" || !d.g) return lobbyView(d, inRoom);
    ({ uno: unoView, ludo: ludoView, chess: chessView })[d.game](d);
    if (d.status === "done") winnerView(d);
  }
  function invite(d) {
    const url = d.link || "", text = `Join my ${NAMES[d.game]} table on Ella! Code ${d.code}`, w = window.Telegram && window.Telegram.WebApp;
    if (url && w && w.openTelegramLink) return w.openTelegramLink("https://t.me/share/url?" + new URLSearchParams({ url, text }));
    (navigator.clipboard ? navigator.clipboard.writeText(url || d.code) : Promise.reject()).then(() => toast("Invite link copied"), () => toast("Code: " + d.code));
  }
  function lobbyView(d, inRoom) {
    const isHost = d.host === d.me, seats = [];
    for (let i = 0; i < d.max; i++) { const p = d.players[i]; seats.push(p ? h("div", { class: "seat" }, ctx.avatar({ display_name: p.name, photo_url: p.photo }), h("span", {}, h("b", {}, p.name), i === 0 ? h("small", {}, "👑 Host") : h("small", {}, "Ready ✓"))) : h("div", { class: "seat empty" }, "Waiting for player…")); }
    inner.replaceChildren(hdr(NAMES[d.game], d.group ? "Group table · " + d.group : "Private table"),
      h("div", { style: "" }, h("span", { class: "code" }, d.code)),
      h("div", { class: "seats" }, seats),
      rules(d, isHost),
      inRoom ? h("div", { class: "acts" },
        h("button", { class: "pbtn full", onclick: () => invite(d) }, "📤 Invite friends"),
        isHost ? h("button", { class: "pbtn full", disabled: d.players.length < d.min, onclick: () => act("start") }, d.players.length < d.min ? `Need ${d.min - d.players.length} more to start` : "Start game") : h("p", { class: "hint" }, "Waiting for the host to start…"),
        h("button", { class: "pbtn ghost full", onclick: async () => { await act("leave"); leaveView(); } }, "Leave table"))
        : h("button", { class: "pbtn full", onclick: () => act("join") }, "Join table"));
  }
  function rules(d, isHost) {
    const R = d.game === "uno" ? [["stack", "Stack +2 / +4"]] : d.game === "ludo" ? [["block", "Blockade (2 tokens = wall)"]] : [];
    return !R.length ? null : h("div", { class: "rules" }, R.map(([k, label]) => h("button", { class: "rule", role: "switch", "aria-checked": String(!!d.opts[k]), disabled: !isHost, onclick: () => act("opt", { k, v: !d.opts[k] }) }, h("span", {}, label), h("i", {}))));
  }
  function turnBar(d, name, mine) {
    const left = h("small", {}); const bar = h("div", { class: "turnbar" + (mine ? " me" : "") }, mine ? "Your turn " : name + "'s turn ", left);
    const tick = () => { const s = Math.max(0, d.deadline - Math.floor(Date.now() / 1000)); left.textContent = d.status === "playing" ? `· ${s}s` : ""; };
    tick(); timer = setInterval(tick, 1000); return bar;
  }
  const by = (d, id) => d.players.find((p) => p.id === id) || { name: "Player" };

  function unoView(d) {
    const g = d.g, mine = g.turn === d.me && d.status === "playing", can = new Set(g.playable);
    const opps = d.players.filter((p) => p.id !== d.me).map((p) => h("div", { class: "opp" + (g.turn === p.id && d.status === "playing" ? " on-turn" : "") }, ctx.avatar({ display_name: p.name, photo_url: p.photo }), h("span", {}, p.name), h("span", { class: "cnt" + (g.counts[p.id] === 1 ? " one" : "") }, g.counts[p.id] === 1 ? "UNO!" : g.counts[p.id] + " cards")));
    const pick = (c) => { if (!mine || !can.has(c)) return tg.haptic("light"); if (c[0] !== "w") return act("play", { card: c }); colorSheet((color) => act("play", { card: c, color })); };
    const hand = h("div", { class: "hand" }, g.hand.map((c) => { const el = unoCard(c, mine ? (can.has(c) ? "can" : "dim") : ""); el.addEventListener("click", () => pick(c)); return el; }));
    const draw = cardBack("pile" + (mine && !g.drawn ? " tap" : "")); draw.classList.add("pile"); draw.addEventListener("click", () => mine && !g.drawn && act("draw"));
    const disc = unoCard(g.top, "disc");
    inner.replaceChildren(hdr("UNO", "Table " + d.code),
      h("div", { class: "felt" }, h("div", { class: "opps" }, opps), h("div", { class: "mid" }, h("span", { class: "ring " + g.color }), draw, disc), turnBar(d, by(d, g.turn).name, mine),
        g.pend ? h("div", { class: "turnbar" }, mine ? `Stack a draw card, or take ${g.pend}` : `+${g.pend} pending`) : null,
        g.drawn && mine ? h("div", { class: "acts two" }, h("button", { class: "pbtn", onclick: () => pick(g.drawn) }, "Play drawn card"), h("button", { class: "pbtn ghost", onclick: () => act("pass") }, "Keep & pass")) : null, hand));
  }
  function colorSheet(done) {
    const sh = h("div", { class: "sheet", onclick: (e) => e.target === sh && sh.remove() }, h("div", { class: "box" }, h("h3", {}, "Choose a colour"),
      h("div", { class: "cols" }, ["r", "y", "g", "b"].map((c) => h("button", { class: c, "aria-label": c, onclick: () => { sh.remove(); done(c); } })))));
    ov.append(sh);
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
      t.classList.toggle("can", movable); t.onclick = movable ? () => act("move", { token: i }) : null; }); });
    [...board.toks.values()].forEach((t) => layer.append(t));
    const dice = h("button", { class: "dice" + (mine && !g.roll ? " can" : ""), "aria-label": "Dice", onclick: () => { if (mine && !g.roll) { dice.classList.add("rolling"); act("roll"); } } },
      Array.from({ length: 9 }, (_, i) => h("b", { class: g.roll && [[], [4], [0, 8], [0, 4, 8], [0, 2, 6, 8], [0, 2, 4, 6, 8], [0, 2, 3, 5, 6, 8]][g.roll].includes(i) ? "on" : "" })));
    const chips = d.players.map((p) => h("div", { class: "me-chip" + (g.turn === p.id && d.status === "playing" ? " on-turn" : "") }, h("i", { class: "dot", "data-c": "" }), ctx.avatar({ display_name: p.name, photo_url: p.photo }), p.name));
    chips.forEach((c, i) => c.querySelector("i").style.background = TOKEN_COLORS[g.seat[d.players[i].id]][2]);
    inner.replaceChildren(hdr("LUDO", "Table " + d.code), h("div", { class: "plrs" }, chips), board.svg, dice, turnBar(d, by(d, g.turn).name, mine),
      h("p", { class: "hint" }, mine && g.roll ? "Tap a glowing token to move." : g.last || "Roll a 6 to bring a token out."));
  }

  function chessView(d) {
    const g = d.g, flip = g.w !== d.me, mine = g.turn === d.me && d.status === "playing", rows = g.b, mySide = g.w === d.me;
    const dests = (r, c) => g.legal.filter((m) => m[0] === r && m[1] === c).map((m) => [m[2], m[3]]);
    const go = (ar, ac) => {
      if (!mine) return;
      const p = rows[ar][ac];
      if (cSel && dests(...cSel).some((x) => x[0] === ar && x[1] === ac)) {
        const from = cSel, pc = rows[from[0]][from[1]], send = (promo) => { cSel = null; act("move", { m: [...from, ar, ac], promo }); };
        return pc.toLowerCase() === "p" && (ar === 0 || ar === 7) ? promoSheet(mySide, send) : send("Q");
      }
      cSel = p !== "." && (p === p.toUpperCase()) === mySide && dests(ar, ac).length ? [ar, ac] : null; render();
    };
    const svg = S("svg", { viewBox: "-4 -4 88 88", class: "board cb", role: "img", "aria-label": "Chess board" },
      S("defs", {}, S("linearGradient", { id: "cl", x1: "0", y1: "0", x2: "1", y2: "1" }, S("stop", { offset: "0", "stop-color": "#f6ecd2" }), S("stop", { offset: "1", "stop-color": "#e2cfa2" })),
        S("linearGradient", { id: "cd", x1: "0", y1: "0", x2: "1", y2: "1" }, S("stop", { offset: "0", "stop-color": "#7a5240" }), S("stop", { offset: "1", "stop-color": "#4d3226" })),
        S("linearGradient", { id: "cf", x1: "0", y1: "0", x2: "1", y2: "1" }, S("stop", { offset: "0", "stop-color": "#ffe3a0" }), S("stop", { offset: "1", "stop-color": "#b98a2e" })),
        S("radialGradient", { id: "ck" }, S("stop", { offset: "0", "stop-color": "#ff3b45", "stop-opacity": ".95" }), S("stop", { offset: "1", "stop-color": "#ff3b45", "stop-opacity": "0" }))),
      S("rect", { x: -4, y: -4, width: 88, height: 88, rx: 5, fill: "url(#cf)" }), S("rect", { x: -1, y: -1, width: 82, height: 82, rx: 2, fill: "#2a1b12" }));
    const to = cSel ? dests(...cSel) : [];
    for (let dr = 0; dr < 8; dr++) for (let dc = 0; dc < 8; dc++) {
      const ar = flip ? 7 - dr : dr, ac = flip ? 7 - dc : dc, p = rows[ar][ac], x = dc * 10, y = dr * 10;
      svg.append(S("rect", { x, y, width: 10, height: 10, fill: (dr + dc) % 2 ? "url(#cd)" : "url(#cl)" }));
      if (g.last && ((g.last[0] === ar && g.last[1] === ac) || (g.last[2] === ar && g.last[3] === ac))) svg.append(S("rect", { x, y, width: 10, height: 10, fill: "#f4c76b", opacity: ".42" }));
      if (cSel && cSel[0] === ar && cSel[1] === ac) svg.append(S("rect", { x, y, width: 10, height: 10, fill: "#6fe3b0", opacity: ".55" }));
      if (g.check && p.toLowerCase() === "k" && (p === p.toUpperCase()) === (g.turn === g.w)) svg.append(S("circle", { cx: x + 5, cy: y + 5, r: 7, fill: "url(#ck)" }));
      if (p !== ".") svg.append(S("text", { x: x + 5, y: y + 7.7, "text-anchor": "middle", "font-size": 8.6, class: "gl " + (p === p.toUpperCase() ? "wp" : "bp") }, PIECE[p.toLowerCase()] + "\uFE0E"));
      if (to.some((t) => t[0] === ar && t[1] === ac)) svg.append(p === "." ? S("circle", { cx: x + 5, cy: y + 5, r: 1.9, fill: "#6fe3b0", opacity: ".9" }) : S("circle", { cx: x + 5, cy: y + 5, r: 4.4, fill: "none", stroke: "#6fe3b0", "stroke-width": 1, opacity: ".95" }));
      const hit = S("rect", { x, y, width: 10, height: 10, fill: "transparent", class: "hit" }); hit.addEventListener("click", () => go(ar, ac)); svg.append(hit);
    }
    const chip = (p) => h("div", { class: "me-chip" + (g.turn === p.id && d.status === "playing" ? " on-turn" : "") }, h("i", { class: "cdot " + (g.w === p.id ? "w" : "b") }), ctx.avatar({ display_name: p.name, photo_url: p.photo }), p.name + (g.w === p.id ? " · White" : " · Black"));
    const opp = d.players.find((p) => p.id !== d.me) || d.players[0], me = d.players.find((p) => p.id === d.me) || d.players[0];
    inner.replaceChildren(hdr("CHESS", "Table " + d.code), h("div", { class: "plrs" }, chip(opp)), svg, h("div", { class: "plrs" }, chip(me)), turnBar(d, by(d, g.turn).name, mine),
      h("p", { class: "hint" }, g.check && d.status === "playing" ? "Check!" : mine ? "Tap a piece, then a highlighted square." : "Waiting for your opponent…"),
      d.status === "playing" ? h("button", { class: "pbtn ghost full", onclick: () => confirmSheet("Resign this game?", "Resign", () => act("resign")) }, "Resign") : null);
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
    const won = d.winner === d.me, draw = d.winner == null, sh = h("div", { class: "sheet" }, h("div", { class: "box" }, h("div", { class: "win" }, draw ? "🤝" : won ? "🏆" : "🎉"),
      h("div", { class: "win-t" }, draw ? "Draw" : won ? "You won!" : by(d, d.winner).name + " won"), d.g && d.g.res ? h("p", { class: "hint" }, d.g.res) : null,
      d.host === d.me ? h("button", { class: "pbtn full", onclick: () => { sh.remove(); act("again"); } }, "Play again") : h("p", { class: "hint" }, "Waiting for the host…"),
      h("button", { class: "pbtn ghost full", onclick: async () => { sh.remove(); await act("leave"); leaveView(); } }, "Leave table")));
    ov.append(sh);
  }

  if (start && start.code) enter(start.code); else hub();
}
