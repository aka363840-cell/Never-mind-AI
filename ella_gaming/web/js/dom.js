// Tiny DOM helpers. Nothing from the network is ever written with innerHTML: only text nodes / attributes.
export function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}

const P = {
  home: '<path d="M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  games: '<path d="M6 11h4M8 9v4M15 12h.01M18 10h.01M17.3 5H6.7a4 4 0 0 0-3.9 3.2l-1.6 7.5A2.5 2.5 0 0 0 3.6 19c.8 0 1.5-.4 2-1l1.5-2h9.8l1.5 2c.5.6 1.2 1 2 1a2.5 2.5 0 0 0 2.4-3.3l-1.6-7.5A4 4 0 0 0 17.3 5z"/>',
  premium: '<path d="M3 8l4.5 4L12 5l4.5 7L21 8l-2 11H5z"/>',
  profile: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  search: '<circle cx="11" cy="11" r="7.5"/><path d="M21 21l-4.3-4.3"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>',
  play: '<path d="M7 4.5v15l13-7.5z" fill="currentColor"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  shrink: '<path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5M12 18h.01"/>',
  offline: '<path d="M2 2l20 20M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 5.2-2.7M19 12.9a10 10 0 0 0-2.3-1.7M2 8.8a15 15 0 0 1 4.2-2.6M22 8.8A15 15 0 0 0 12 5M12 20h.01"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.4 8.4-8 9-4.6-.6-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  sparkle: '<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>',
  // categories
  arcade: '<circle cx="12" cy="6.5" r="2.5"/><path d="M12 9v5"/><rect x="4" y="14" width="16" height="6" rx="2"/><path d="M8 17h.01M16 17h.01"/>',
  puzzle: '<path d="M9 3h4v3a2 2 0 1 0 0 4h0v2h3a2 2 0 1 1 4 0v4h-4v3H9v-3a2 2 0 1 0-4 0v-4h4z" transform="translate(0 0)"/>',
  racing: '<path d="M5 21V4M5 4h13l-2.5 4L18 12H5"/>',
  sports: '<circle cx="12" cy="12" r="9"/><path d="M12 3c3 3 3 15 0 18M3 12c4-2.5 14-2.5 18 0"/>',
  action: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  adventure: '<circle cx="12" cy="12" r="9"/><path d="M15.5 8.5L13.5 13.5 8.5 15.5l2-5z"/>',
  strategy: '<path d="M5 20h14M6 20l1-9-2-4 4 2 3-4 3 4 4-2-2 4 1 9"/>',
  board: '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M9.2 3.5v17M14.8 3.5v17M3.5 9.2h17M3.5 14.8h17"/>',
  casual: '<circle cx="12" cy="12" r="9"/><path d="M8 14a4.5 4.5 0 0 0 8 0M9 9.5h.01M15 9.5h.01"/>',
  multiplayer: '<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20v-1.5A4.5 4.5 0 0 1 7 14h4a4.5 4.5 0 0 1 4.5 4.5V20"/><path d="M16 4.9a3.2 3.2 0 0 1 0 6.2M18 14.3a4.5 4.5 0 0 1 3.5 4.2V20"/>',
  card: '<rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M12 8c-2 2.5-3.5 3.5-3.5 5a2 2 0 0 0 3.5 1.2A2 2 0 0 0 15.5 13c0-1.500-1.500-2.500-3.500-5zM12 14.500V17"/>',
  word: '<path d="M4 19L10 5l6 14M6.200 14.500h7.600M19 8v11M19 8h1.500"/>',
};
export function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.9");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  if (cls) s.setAttribute("class", cls);
  s.innerHTML = P[name] || P.sparkle;           // trusted constant strings only
  return s;
}
