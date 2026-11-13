from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M


def kb(rows: list[list[B]]) -> M:
    return M(inline_keyboard=rows)


def back(target: str = "main") -> list[B]:
    return [B(text="⬅️ Menu" if target == "main" else "⬅️ Back", callback_data=f"nav:{target}")]


def main_menu() -> M:
    return kb([
        [B(text="👤 Profile", callback_data="nav:profile"), B(text="💎 Balance", callback_data="nav:balance")],
        [B(text="⚔️ Combat", callback_data="nav:combat"), B(text="⚡ Powers", callback_data="nav:powers")],
        [B(text="🛒 Shop", callback_data="nav:shop"), B(text="🎒 Inventory", callback_data="inv:0")],
        [B(text="🎯 Missions", callback_data="nav:missions"), B(text="🏆 Rankings", callback_data="lb:rich:0")],
        [B(text="🏰 Clan", callback_data="nav:clan"), B(text="🐾 Pets & Hunt", callback_data="nav:pet")],
        [B(text="🗺 Realm: Dungeon & Heists", callback_data="nav:realm")],
        [B(text="👨‍👩‍👧‍👦 Love & Family", callback_data="nav:family"), B(text="🎉 Fun", callback_data="nav:fun")],
        [B(text="🎰 Casino", callback_data="nav:casino"), B(text="🤖 AI Chat", callback_data="nav:ai")],
        [B(text="🕹 Arcade & Social", callback_data="nav:arcade")],
        [B(text="📖 Commands", callback_data="nav:help"), B(text="📜 Rules", callback_data="rules:home")],
        [B(text="ℹ️ About", callback_data="nav:about"), B(text="⚙️ Settings", callback_data="nav:settings")],
    ])
