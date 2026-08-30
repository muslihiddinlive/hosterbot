from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
import database as db

# ---------- Reply keyboard (approve bo'lgan userlar uchun asosiy menu) ----------

def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📩 Adminga habar berish")],
        [KeyboardButton(text="🤖 Mening botlarim"), KeyboardButton(text="➕ Bot qo'shish")],
        [KeyboardButton(text="💳 Hisob")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Admin panel")])
        rows.append([KeyboardButton(text="🗄 DB backup tekshirish")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="⛔️ Bekor qilish")]], resize_keyboard=True)


def self_service_menu_kb() -> ReplyKeyboardMarkup:
    """Admin tomonidan hali tasdiqlanmagan (pending/yangi/denied) foydalanuvchilar uchun —
    ular admin tasdig'isiz ham Stars orqali o'zlari bot host qila olishlari kerak."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Hisob")],
            [KeyboardButton(text="🤖 Mening botlarim")],
            [KeyboardButton(text="📩 Adminga habar berish")],
        ],
        resize_keyboard=True,
    )


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

def hisob_kb(hosted_bots) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Balansni to'ldirish (Stars)", callback_data="stars_topup")]]
    for b in hosted_bots:
        label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
        rows.append([InlineKeyboardButton(
            text=f"🔁 {label} — yana +24 soat ({b['bot_id']})",
            callback_data=f"stars_extend:{b['bot_id']}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_stars_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Stars miqdorini o'zgartirish", callback_data="admin_set_stars_amount")],
        [InlineKeyboardButton(text="✏️ Muddatni o'zgartirish (soat)", callback_data="admin_set_stars_hours")],
        [InlineKeyboardButton(text="✏️ Standart blok muddati (soat)", callback_data="admin_set_block_hours")],
        [InlineKeyboardButton(text="✏️ Min. yechish miqdori", callback_data="admin_set_min_withdraw")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")],
    ])


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
    rows = [
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
    ]
    if bot_row["stars_hosted"]:
        rows.append([InlineKeyboardButton(
            text=f"🔁 Yana 24 soatga uzaytirish ({db.get_stars_per_unit()}⭐️)", callback_data=f"stars_extend:{bot_row['bot_id']}",
        )])
    rows.append([
        InlineKeyboardButton(text="💾 Resurs", callback_data=f"bot_resource:{bot_row['bot_id']}"),
        InlineKeyboardButton(text="📡 Live log", callback_data=f"bot_live_log:{bot_row['bot_id']}"),
    ])
    rows.append([InlineKeyboardButton(text="ℹ️ Bot haqida (kod/log/env)", callback_data=f"bot_info:{bot_row['bot_id']}")])
    rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"bot_delete:{bot_row['bot_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="bot_list_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel_kb(is_superadmin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton(text="📋 Barcha botlar", callback_data="admin_all_bots")],
        [InlineKeyboardButton(text="➕ Bot deploy qilish (admin nomidan)", callback_data="admin_add_bot")],
    ]
    if is_superadmin:
        rows.append([InlineKeyboardButton(text="⭐️ Stars narxi sozlamalari", callback_data="admin_stars_settings")])
        rows.append([InlineKeyboardButton(text="💰 Bot Stars balansi (real)", callback_data="admin_real_balance")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_users_kb(users) -> InlineKeyboardMarkup:
    status_icon = {"approved": "✅", "pending": "⏳", "denied": "⛔️"}
    rows = []
    for u in users:
        label = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["telegram_id"]))
        icon = "🚫" if u["is_banned"] else status_icon.get(u["status"], "❓")
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"admin_user_view:{u['telegram_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_view_kb(telegram_id: int, bots, current_max_bots=None, balance=0, min_withdraw=15, is_banned=False, paid_before=False, viewer_is_superadmin=True, is_approved=False) -> InlineKeyboardMarkup:
    rows = []
    for b in bots:
        label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
        status_icon = "🟢" if b["status"] == "running" else ("🟡" if b["status"] == "crashed" else "🔴")
        rows.append([InlineKeyboardButton(text=f"{status_icon} {label}", callback_data=f"admin_bot_view:{b['bot_id']}")])
    limit_label = f"✏️ Bot limiti ({current_max_bots if current_max_bots is not None else 'default'})"
    rows.append([InlineKeyboardButton(text=limit_label, callback_data=f"admin_set_limit:{telegram_id}")])
    if balance >= min_withdraw:
        rows.append([InlineKeyboardButton(text="🎁 Balansni gift orqali yechish", callback_data=f"admin_gift_withdraw:{telegram_id}")])
    if is_banned:
        rows.append([InlineKeyboardButton(text="✅ Host huquqini qaytarish", callback_data=f"admin_unban:{telegram_id}")])
    elif paid_before and not viewer_is_superadmin:
        rows.append([InlineKeyboardButton(text="🔒 Bloklash (faqat superadmin, to'lov qilgan)", callback_data=f"admin_ban_ask:{telegram_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Host huquqini vaqtincha olib qo'yish", callback_data=f"admin_ban_ask:{telegram_id}")])
    # Faqat admin TASDIQLAB bergan ("approved") huquqni, aynan shu yo'l bilan
    # (admin qarori bilan) qaytarib olish — Stars orqali to'lash huquqiga tegmaydi,
    # shuning uchun bloklashdan (yuqoridagi tugma) farqli, foydalanuvchi keyin
    # Stars to'lab yana ishlata oladi.
    if is_approved and not is_banned:
        rows.append([InlineKeyboardButton(
            text="🗑 Host huquqini butunlay olib qo'yish (admin bergani)",
            callback_data=f"admin_revoke_ask:{telegram_id}",
        )])
    rows.append([InlineKeyboardButton(text="✉️ Habar yozish", callback_data=f"admin_msg_user:{telegram_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_revoke_choice_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, huquqini olib tashla", callback_data=f"admin_revoke_do:{telegram_id}")],
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data=f"admin_user_view:{telegram_id}")],
    ])


def admin_ban_choice_kb(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, standart shartlar bilan", callback_data=f"admin_ban_do:{telegram_id}")],
        [InlineKeyboardButton(text="⏱ Maxsus muddat belgilash (soat)", callback_data=f"admin_ban_custom_hours:{telegram_id}")],
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data=f"admin_user_view:{telegram_id}")],
    ])


def admin_gift_list_kb(telegram_id: int, gifts) -> InlineKeyboardMarkup:
    rows = []
    for g in gifts:
        emoji = g.sticker.emoji if getattr(g, "sticker", None) and getattr(g.sticker, "emoji", None) else "🎁"
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {g.star_count} ⭐️",
            callback_data=f"admin_gift_send:{telegram_id}:{g.id}:{g.star_count}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_user_view:{telegram_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_self_gift_list_kb(gifts) -> InlineKeyboardMarkup:
    rows = []
    for g in gifts:
        emoji = g.sticker.emoji if getattr(g, "sticker", None) and getattr(g.sticker, "emoji", None) else "🎁"
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {g.star_count} ⭐️",
            callback_data=f"admin_self_gift_send:{g.id}:{g.star_count}",
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_real_balance")])
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
        [
            InlineKeyboardButton(text="💾 Resurs", callback_data=f"bot_resource:{bot_row['bot_id']}"),
            InlineKeyboardButton(text="📡 Live log", callback_data=f"bot_live_log:{bot_row['bot_id']}"),
        ],
        [InlineKeyboardButton(text="ℹ️ Kod/log/env", callback_data=f"bot_info:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"bot_delete:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_all_bots")],
    ])


def crash_notify_kb(bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Qayta ishga tushirish", callback_data=f"bot_start:{bot_id}")],
    ])


def owner_info_kb(owner_id: int, bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Habar yozish", callback_data=f"admin_msg_user:{owner_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_bot_view:{bot_id}")],
    ])
