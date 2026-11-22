// Premium Games: look & feel (card backs, board themes), sound (WebAudio, no files) and haptics. Prefs live in localStorage (try/catch).
import { h } from "./dom.js";
import { tg } from "./tg.js";

const KEY = "ella.pg.prefs";
export const prefs = { cb: "1", bt: "classic", snd: true, hap: true };
try { Object.assign(prefs, JSON.parse(localStorage.getItem(KEY) || "{}")); } catch {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch {} };

export const CARD_BACKS = [["1", "Midnight"], ["2", "Ocean"], ["3", "Rose gold"]];
export const BOARDS = [["classic", "Classic"], ["emerald", "Emerald"], ["slate", "Slate"]];
export const themeAttrs = (el) => { el.dataset.cb = prefs.cb; el.dataset.bt = prefs.bt; };

let ac = null;
function tone(f, dur, type = "sine", vol = 0.13, delay = 0) {
  if (!prefs.snd) return;
  try {
    ac = ac || new (window.AudioContext || window.webkitAudioContext)();
    if (ac.state === "suspended") ac.resume();
    const t = ac.currentTime + delay, o = ac.createOscillator(), g = ac.createGain();
    o.type = type; o.frequency.setValueAtTime(f, t);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(vol, t + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(ac.destination); o.start(t); o.stop(t + dur + 0.02);
  } catch {}
}
export const sfx = {
  move: () => tone(330, 0.09, "triangle"),
  capture: () => { tone(220, 0.12, "square", 0.1); tone(140, 0.16, "triangle", 0.12, 0.05); },
  card: () => { tone(520, 0.05, "triangle"); tone(390, 0.07, "triangle", 0.1, 0.04); },
  dice: () => { for (let i = 0; i < 5; i++) tone(200 + Math.random() * 260, 0.05, "square", 0.06, i * 0.09); },
  turn: () => { tone(660, 0.1, "sine"); tone(880, 0.14, "sine", 0.12, 0.1); },
  alert: () => { tone(880, 0.08, "square", 0.1); tone(880, 0.08, "square", 0.1, 0.12); },
  win: () => [523, 659, 784, 1047].forEach((f, i) => tone(f, 0.22, "triangle", 0.14, i * 0.12)),
  lose: () => [392, 330, 262].forEach((f, i) => tone(f, 0.25, "sine", 0.12, i * 0.14)),
};
export const buzz = (kind = "light") => { if (prefs.hap) tg.haptic(kind); };
export const notify = (kind = "success") => { if (prefs.hap) tg.notify(kind); };

export function settingsSheet(ov, onChange) {
  const sh = h("div", { class: "sheet", onclick: (e) => e.target === sh && sh.remove() });
  const draw = () => {
    const pick = (list, k) => h("div", { class: "seg" }, list.map(([v, label]) => h("button", { "aria-pressed": String(prefs[k] === v), onclick: () => { prefs[k] = v; save(); onChange(); draw(); } }, label)));
    const sw = (k, label) => h("button", { class: "rule", role: "switch", "aria-checked": String(!!prefs[k]), onclick: () => { prefs[k] = !prefs[k]; save(); if (k === "snd" && prefs.snd) sfx.turn(); draw(); } }, h("span", {}, label), h("i", {}));
    sh.replaceChildren(h("div", { class: "box set" }, h("h3", {}, "Look & feel"),
      h("p", { class: "lab" }, "Card back"), pick(CARD_BACKS, "cb"),
      h("p", { class: "lab" }, "Chess board"), pick(BOARDS, "bt"),
      h("div", { class: "rules" }, sw("snd", "Sound"), sw("hap", "Vibration")),
      h("button", { class: "pbtn full", onclick: () => sh.remove() }, "Done")));
  };
  draw(); ov.append(sh);
}
