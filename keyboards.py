from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ---------- Reply keyboard (approve bo'lgan userlar uchun asosiy menu) ----------

def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📩 Adminga habar berish")],
        [KeyboardButton(text="🤖 Mening botlarim"), KeyboardButton(text="➕ Bot qo'shish")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])
        rows.append([KeyboardButton(text="🗄 DB backup tekshirish")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⛔️ Bekor qilish")]], resize_keyboard=True)


def skip_requirements_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➡️ O'tkazib yuborish (kerak emas)")],
            [KeyboardButton(text="⛔️ Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def auto_build_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Avtomatik (tavsiya etiladi)")],
            [KeyboardButton(text="⛔️ Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def skip_or_add_env_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➡️ O'tkazib yuborish (ENV kerak emas)")],
            [KeyboardButton(text="⛔️ Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def env_added_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Yana ENV qo'shish")],
            [KeyboardButton(text="✅ Tugatish va deploy qilish")],
            [KeyboardButton(text="⛔️ Bekor qilish")],
        ],
        resize_keyboard=True,
    )


# ---------- Inline keyboards ----------

def admin_review_kb(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Bekor qilish va habar berish", callback_data=f"req_deny:{request_id}"),
        ],
        [
            InlineKeyboardButton(text="✅ Ruxsat berish va habar berish", callback_data=f"req_approve:{request_id}"),
        ],
        [
            InlineKeyboardButton(text="💬 Habar berish", callback_data=f"req_reply:{request_id}"),
        ],
    ])


def my_bots_list_kb(bots) -> InlineKeyboardMarkup:
    rows = []
    for b in bots:
        label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
        status_icon = "🟢" if b["status"] == "running" else "🔴"
        rows.append([InlineKeyboardButton(text=f"{status_icon} {label}", callback_data=f"bot_manage:{b['bot_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bot_manage_kb(bot_row) -> InlineKeyboardMarkup:
    running = bot_row["status"] == "running"
    toggle_text = "⏹ To'xtatish" if running else "▶️ Ishga tushirish"
    toggle_cb = f"bot_stop:{bot_row['bot_id']}" if running else f"bot_start:{bot_row['bot_id']}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
        [InlineKeyboardButton(text="ℹ️ Bot haqida (kod/log/env)", callback_data=f"bot_info:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"bot_delete:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="bot_list_back")],
    ])


def admin_panel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton(text="📋 Barcha botlar", callback_data="admin_all_bots")],
        [InlineKeyboardButton(text="➕ Bot deploy qilish (admin nomidan)", callback_data="admin_add_bot")],
    ])


def admin_users_kb(users) -> InlineKeyboardMarkup:
    status_icon = {"approved": "✅", "pending": "⏳", "denied": "⛔️"}
    rows = []
    for u in users:
        label = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["telegram_id"]))
        icon = status_icon.get(u["status"], "❓")
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"admin_user_view:{u['telegram_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_view_kb(telegram_id: int, bots) -> InlineKeyboardMarkup:
    rows = []
    for b in bots:
        label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
        status_icon = "🟢" if b["status"] == "running" else ("🟡" if b["status"] == "crashed" else "🔴")
        rows.append([InlineKeyboardButton(text=f"{status_icon} {label}", callback_data=f"admin_bot_view:{b['bot_id']}")])
    rows.append([InlineKeyboardButton(text="✉️ Habar yozish", callback_data=f"admin_msg_user:{telegram_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_all_bots_kb(bots) -> InlineKeyboardMarkup:
    rows = []
    for b in bots:
        label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
        status_icon = "🟢" if b["status"] == "running" else "🔴"
        rows.append([InlineKeyboardButton(text=f"{status_icon} {label}", callback_data=f"admin_bot_view:{b['bot_id']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_bot_view_kb(bot_row) -> InlineKeyboardMarkup:
    running = bot_row["status"] == "running"
    toggle_text = "⏹ To'xtatish" if running else "▶️ Ishga tushirish"
    toggle_cb = f"bot_stop:{bot_row['bot_id']}" if running else f"bot_start:{bot_row['bot_id']}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Egasi haqida", callback_data=f"admin_owner_info:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
        [InlineKeyboardButton(text="ℹ️ Kod/log/env", callback_data=f"bot_info:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"bot_delete:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_all_bots")],
    ])


def owner_info_kb(owner_id: int, bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Habar yozish", callback_data=f"admin_msg_user:{owner_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_bot_view:{bot_id}")],
    ])
