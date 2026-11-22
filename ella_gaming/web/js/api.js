import { API_BASE } from "./config.js";
import { tg } from "./tg.js";

let token = null;
let loginPromise = null;

export class ApiError extends Error {
  constructor(status, code, message) { super(message || code); this.status = status; this.code = code; }
}

export async function login() {
  if (!loginPromise) {
    loginPromise = (async () => {
      const r = await raw("POST", "/api/auth/telegram", { initData: tg.initData }, false);
      token = r.token;
      return r;
    })().finally(() => { loginPromise = null; });
  }
  return loginPromise;
}

async function raw(method, path, body, withAuth = true, opts = {}) {
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(withAuth && token ? { Authorization: "Bearer " + token } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts.signal, keepalive: !!opts.keepalive,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(0, "network", "No connection to Ella Gaming");
  }
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw new ApiError(res.status, (data && data.error) || "error", (data && data.message) || `Request failed (${res.status})`);
  return data;
}

// Authenticated call: transparently re-logs in once when the session token expired. Retries idempotent reads once on network errors.
export async function call(method, path, body, opts = {}) {
  if (!token) await login();
  try {
    return await raw(method, path, body, true, opts);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      await login();
      return raw(method, path, body, true, opts);
    }
    if (e instanceof ApiError && e.code === "network" && method === "GET") {
      await new Promise((r) => setTimeout(r, 700));
      return raw(method, path, body, true, opts);
    }
    throw e;
  }
}

export const api = {
  me: () => call("GET", "/api/me"),
  patchMe: (nickname) => call("PATCH", "/api/me", { nickname }),
  home: () => call("GET", "/api/home"),
  game: (slug) => call("GET", `/api/games/${encodeURIComponent(slug)}`),
  games: (params, opts) => call("GET", "/api/games?" + new URLSearchParams(params), undefined, opts),
  setFav: (slug, on) => call(on ? "PUT" : "DELETE", `/api/favorites/${encodeURIComponent(slug)}`),
  startSession: (slug, platform) => call("POST", "/api/sessions", { slug, platform: (platform || "").toLowerCase() }),
  ping: (id) => call("POST", `/api/sessions/${id}/ping`, {}, { keepalive: true }),
  end: (id) => call("POST", `/api/sessions/${id}/end`, {}, { keepalive: true }),
};
