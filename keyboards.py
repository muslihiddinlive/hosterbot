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
        [KeyboardButton(text="💳 Hisob"), KeyboardButton(text="❓ Yordam")],
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
            [KeyboardButton(text="📩 Adminga habar berish"), KeyboardButton(text="❓ Yordam")],
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
    rows = [
        [InlineKeyboardButton(text="➕ Balansni to'ldirish (Stars)", callback_data="stars_topup")],
        [InlineKeyboardButton(text="📜 To'lov tarixi", callback_data="stars_history")],
    ]
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
        [InlineKeyboardButton(text="🤖 AI yordam narxi", callback_data="admin_set_ai_help_price")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")],
    ])


def admin_ai_providers_kb(providers) -> InlineKeyboardMarkup:
    rows = []
    for p in providers:
        icon = "🟢" if p["is_active"] else "⚪️"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {p['name']} (ustuvorlik: {p['priority']})",
            callback_data=f"admin_ai_provider_view:{p['provider_id']}",
        )])
    rows.append([InlineKeyboardButton(text="➕ Yangi provayder qo'shish", callback_data="admin_ai_provider_add")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_ai_provider_view_kb(provider) -> InlineKeyboardMarkup:
    toggle_text = "⏸ Nofaol qilish" if provider["is_active"] else "▶️ Faollashtirish"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin_ai_provider_toggle:{provider['provider_id']}")],
        [InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"admin_ai_provider_delete:{provider['provider_id']}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_ai_providers")],
    ])


def admin_ai_provider_kind_kb() -> InlineKeyboardMarkup:
    """AI provayder qo'shishning birinchi qadami — Cloudflare uchun soddalashtirilgan
    oqim (faqat Account ID so'raladi, URL avtomatik yasaladi) yoki boshqa har qanday
    OpenAI-compatible xizmat uchun to'liq URL so'raladigan oqim."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☁️ Cloudflare Workers AI", callback_data="admin_ai_kind:cloudflare")],
        [InlineKeyboardButton(text="🔧 Boshqa (custom URL)", callback_data="admin_ai_kind:custom")],
    ])


CLOUDFLARE_CODER_MODELS = [
    ("Qwen2.5-Coder 32B (bepul limitga kiradi, tavsiya)", "@cf/qwen/qwen2.5-coder-32b-instruct"),
    ("Kimi K2.7 Code (kuchliroq, faqat Paid plan)", "@cf/moonshotai/kimi-k2.7-code"),
    ("GLM-5.3 (kuchli, faqat Paid plan)", "@cf/zai-org/glm-5.3"),
]


def admin_ai_cloudflare_model_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"admin_ai_cf_model:{model}")]
            for label, model in CLOUDFLARE_CODER_MODELS]
    rows.append([InlineKeyboardButton(text="✏️ Boshqa model nomi yozaman", callback_data="admin_ai_cf_model:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def bot_manage_kb(bot_row, has_env: bool = False) -> InlineKeyboardMarkup:
    running = bot_row["status"] == "running"
    crashed = bot_row["status"] == "crashed"
    toggle_text = "⏹ To'xtatish" if running else "▶️ Ishga tushirish"
    toggle_cb = f"bot_stop:{bot_row['bot_id']}" if running else f"bot_start:{bot_row['bot_id']}"
    rows = [
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
    ]
    if crashed:
        # Oddiy "Ishga tushirish" faqat mavjud muhitda qayta ishga tushiradi — agar
        # crash sababi build/kutubxona muammosi bo'lsa yordam bermaydi. Shu sabab
        # alohida "qayta build bilan" variantini ham ko'rsatamiz.
        rows.append([InlineKeyboardButton(
            text="🔁 Qayta build qilib urinish", callback_data=f"bot_rebuild:{bot_row['bot_id']}",
        )])
        # AI-tashxis faqat "nega qulab tushdi" savoliga javob beradi, shu sabab
        # faqat crashed holatda ma'noli — boshqa holatda ko'rsatilmaydi.
        rows.append([InlineKeyboardButton(
            text=f"🤖 AI yordam ({db.get_ai_help_price_stars()}⭐️)", callback_data=f"ai_help:{bot_row['bot_id']}",
        )])
    # "🛠 Botni tahrirlash" (kod/requirements/env almashtirish) botning holatidan
    # QAT'I NAZAR har doim ko'rsatiladi — foydalanuvchi ishlab turgan botni ham
    # yangilashi mumkin bo'lishi kerak, faqat crash bo'lgandagina emas.
    rows.append([InlineKeyboardButton(
        text="🛠 Botni tahrirlash", callback_data=f"edit_bot_menu:{bot_row['bot_id']}",
    )])
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


def edit_bot_menu_kb(bot_id: int, has_env: bool = False) -> InlineKeyboardMarkup:
    """'🛠 Botni tahrirlash' bosilganda ochiladigan pastki menyu — kod/requirements/env
    almashtirish tugmalari (fix_code/fix_reqs/fix_env handler'lari bilan bir xil,
    holat crashed/running/stopped bo'lishidan qat'i nazar ishlaydi)."""
    rows = [
        [InlineKeyboardButton(text="📄 Kodni almashtirish", callback_data=f"fix_code:{bot_id}")],
        [InlineKeyboardButton(text="📋 requirements.txt almashtirish", callback_data=f"fix_reqs:{bot_id}")],
    ]
    if has_env:
        rows.append([InlineKeyboardButton(text="🔑 ENV tahrirlash", callback_data=f"fix_env:{bot_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"bot_manage:{bot_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_panel_kb(is_superadmin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton(text="📋 Barcha botlar", callback_data="admin_all_bots")],
        [InlineKeyboardButton(text="➕ Bot deploy qilish (admin nomidan)", callback_data="admin_add_bot")],
        [InlineKeyboardButton(text="📢 Barcha userlarga xabar", callback_data="admin_broadcast_ask")],
    ]
    if is_superadmin:
        rows.append([InlineKeyboardButton(text="⭐️ Stars narxi sozlamalari", callback_data="admin_stars_settings")])
        rows.append([InlineKeyboardButton(text="💰 Bot Stars balansi (real)", callback_data="admin_real_balance")])
        rows.append([InlineKeyboardButton(text="🤖 AI provayderlar", callback_data="admin_ai_providers")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_users_kb(users) -> InlineKeyboardMarkup:
    status_icon = {"approved": "✅", "pending": "⏳", "denied": "⛔️"}
    rows = [[InlineKeyboardButton(text="🔍 Qidirish (ID yoki username)", callback_data="admin_search_user_ask")]]
    for u in users:
        label = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["telegram_id"]))
        icon = "🚫" if u["is_banned"] else status_icon.get(u["status"], "❓")
        rows.append([InlineKeyboardButton(text=f"{icon} {label}", callback_data=f"admin_user_view:{u['telegram_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_view_kb(telegram_id: int, bots, current_max_bots=None, balance=0, min_withdraw=15, is_banned=False, paid_before=False, viewer_is_superadmin=True) -> InlineKeyboardMarkup:
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
        rows.append([InlineKeyboardButton(text="🚫 Host huquqini olib qo'yish", callback_data=f"admin_ban_ask:{telegram_id}")])
    rows.append([InlineKeyboardButton(text="✉️ Habar yozish", callback_data=f"admin_msg_user:{telegram_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_sender_choice_kb(callback_prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Admin sifatida", callback_data=f"{callback_prefix}:admin")],
        [InlineKeyboardButton(text="👑 Ega sifatida", callback_data=f"{callback_prefix}:owner")],
    ])


def admin_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Ha, yubor", callback_data="admin_broadcast_confirm")],
        [InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="admin_panel_back")],
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
    crashed = bot_row["status"] == "crashed"
    toggle_text = "⏹ To'xtatish" if running else "▶️ Ishga tushirish"
    toggle_cb = f"bot_stop:{bot_row['bot_id']}" if running else f"bot_start:{bot_row['bot_id']}"
    rows = [
        [InlineKeyboardButton(text="👤 Egasi haqida", callback_data=f"admin_owner_info:{bot_row['bot_id']}")],
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
    ]
    if crashed:
        rows.append([InlineKeyboardButton(
            text="🔁 Qayta build qilib urinish", callback_data=f"bot_rebuild:{bot_row['bot_id']}",
        )])
    rows.append([
        InlineKeyboardButton(text="💾 Resurs", callback_data=f"bot_resource:{bot_row['bot_id']}"),
        InlineKeyboardButton(text="📡 Live log", callback_data=f"bot_live_log:{bot_row['bot_id']}"),
    ])
    rows.append([InlineKeyboardButton(text="ℹ️ Kod/log/env", callback_data=f"bot_info:{bot_row['bot_id']}")])
    rows.append([InlineKeyboardButton(text="🧪 Shaxsiy test-deploy (o'z tokening bilan)", callback_data=f"admin_test_deploy:{bot_row['bot_id']}")])
    rows.append([InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"bot_delete:{bot_row['bot_id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_all_bots")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crash_notify_kb(bot_id: int, has_env: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔄 Qayta ishga tushirish", callback_data=f"bot_start:{bot_id}")],
        [InlineKeyboardButton(text="🔁 Qayta build qilib urinish", callback_data=f"bot_rebuild:{bot_id}")],
        [
            InlineKeyboardButton(text="📄 Kodni almashtirish", callback_data=f"fix_code:{bot_id}"),
            InlineKeyboardButton(text="📋 requirements.txt", callback_data=f"fix_reqs:{bot_id}"),
        ],
    ]
    env_row = []
    if has_env:
        env_row.append(InlineKeyboardButton(text="🔑 ENV tahrirlash", callback_data=f"fix_env:{bot_id}"))
    env_row.append(InlineKeyboardButton(
        text=f"🤖 AI yordam ({db.get_ai_help_price_stars()}⭐️)", callback_data=f"ai_help:{bot_id}",
    ))
    rows.append(env_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_topics_kb(topics: dict) -> InlineKeyboardMarkup:
    """topics: {topic_id: (sarlavha, matn)} — help_faq.FAQ_TOPICS bilan bir xil shakl."""
    rows = [[InlineKeyboardButton(text=title, callback_data=f"help_topic:{topic_id}")]
            for topic_id, (title, _) in topics.items()]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Yordam menyusiga qaytish", callback_data="help_back")],
    ])


def owner_info_kb(owner_id: int, bot_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Habar yozish", callback_data=f"admin_msg_user:{owner_id}")],
        [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"admin_bot_view:{bot_id}")],
    ])
