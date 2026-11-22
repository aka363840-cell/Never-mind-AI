// Thin wrapper around the official Telegram Mini App SDK (https://core.telegram.org/bots/webapps).
// Every call is feature-checked: older Telegram versions simply skip what they do not support.
const W = window.Telegram && window.Telegram.WebApp;
const ver = (v) => { try { return !!(W && W.isVersionAtLeast(v)); } catch { return false; } };

export const tg = {
  available: !!(W && W.initData),
  initData: W ? W.initData : "",
  platform: W ? (W.platform || "") : "",
  user: W && W.initDataUnsafe ? W.initDataUnsafe.user : null,   // display hint only - the server never trusts this
  init() {
    if (!W) return;
    try { W.ready(); } catch {}
    try { W.expand(); } catch {}
    try { if (ver("6.1")) { W.setHeaderColor("#0c0811"); W.setBackgroundColor("#0c0811"); } } catch {}
    try { if (ver("7.10")) W.setBottomBarColor("#160f1f"); } catch {}
  },
  setBack(handler) {
    if (!W || !W.BackButton) return;
    try {
      if (this._backFn) W.BackButton.offClick(this._backFn);
      this._backFn = handler;
      if (handler) { W.BackButton.onClick(handler); W.BackButton.show(); } else W.BackButton.hide();
    } catch {}
  },
  playing(on) {                       // while a game is open: no swipe-down-to-close, ask before closing
    if (!W) return;
    try { if (ver("7.7")) on ? W.disableVerticalSwipes() : W.enableVerticalSwipes(); } catch {}
    try { on ? W.enableClosingConfirmation() : W.disableClosingConfirmation(); } catch {}
  },
  canFullscreen() { return ver("8.0") && typeof W.requestFullscreen === "function"; },
  isFullscreen() { return !!(W && W.isFullscreen); },
  requestFullscreen() { try { W.requestFullscreen(); return true; } catch { return false; } },
  exitFullscreen() { try { if (W && W.isFullscreen) W.exitFullscreen(); } catch {} },
  onFullscreenChange(fn) { try { if (W && ver("8.0")) { W.onEvent("fullscreenChanged", fn); W.onEvent("fullscreenFailed", fn); } } catch {} },
  haptic(kind = "light") { try { if (ver("6.1")) W.HapticFeedback.impactOccurred(kind); } catch {} },
  notify(kind = "success") { try { if (ver("6.1")) W.HapticFeedback.notificationOccurred(kind); } catch {} },
  close() { try { W.close(); } catch {} },
};
