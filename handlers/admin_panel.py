import html
import time
import logging
import asyncio
import json
import shutil
import os
import re
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin, is_superadmin, STORAGE_GROUP_ID, ADMIN_IDS, SUPERADMIN_IDS, MAX_BOTS_PER_USER
from states import AdminMessageUser, AdminSetLimit, AdminStarsSetting, AdminBanCustomHours, AdminBroadcast, AdminTestDeploy, AdminSearchUser, AdminAIProvider
from keyboards import admin_all_bots_kb, admin_bot_view_kb, owner_info_kb, admin_users_kb, admin_user_view_kb, admin_panel_kb, admin_stars_settings_kb, admin_gift_list_kb, admin_self_gift_list_kb, admin_ban_choice_kb, admin_broadcast_confirm_kb, admin_sender_choice_kb, admin_ai_providers_kb, admin_ai_provider_view_kb, admin_ai_provider_kind_kb, admin_ai_cloudflare_model_kb, admin_search_choice_kb, admin_search_qwerty_kb
from services.deploy_manager import stop_bot_process, start_bot_process, run_build_command, is_running, read_log_tail
from services.file_utils import bot_workdir
from services.resource_monitor import can_start_new_bot, bot_ram_mb
from aiogram.exceptions import TelegramBadRequest
from services.backup import backup_database

router = Router()
log = logging.getLogger("hosterbot")

# Superadmin o'ziga gift qilib yechishi mumkin bo'lgan YAGONA gift narxi
# (⭐️ da) — "Army Teddy" (granata ko'targan pluh ayiqcha). Telegram gift
# obyektida nom bo'lmagani uchun narx bo'yicha aniqlanadi.
ALLOWED_SELF_GIFT_STAR_COST = 50


@router.message(F.text == "🗄 DB backup tekshirish")
async def cb_test_backup(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        return
    await message.answer("⏳ Tekshirilmoqda...")
    ok, error = await backup_database(bot)
    if ok:
        await message.answer(
            f"✅ Backup muvaffaqiyatli yuborildi va pin qilindi.\n"
            f"Guruhni tekshiring: <code>{STORAGE_GROUP_ID}</code>",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            "❌ Backup <b>yuborilmadi</b>. Aniq xato:\n"
            f"<code>{html.escape(error or 'nomaʼlum xato')}</code>\n\n"
            "Eng ko'p uchraydigan sabablar:\n"
            f"• Bot <code>{STORAGE_GROUP_ID}</code> guruhiga umuman qo'shilmagan\n"
            "• Bot guruhda admin emas (yoki xabar yuborish/pin qilish huquqi yo'q)\n"
            "• <code>STORAGE_GROUP_ID</code> noto'g'ri (guruh ID minus bilan boshlanishi kerak, masalan -100...)",
            parse_mode="HTML",
        )


@router.callback_query(F.data == "admin_panel_back")
async def cb_admin_panel_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    await callback.message.edit_text("Admin panel:", reply_markup=admin_panel_kb(is_superadmin=is_superadmin(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == "admin_real_balance")
async def cb_admin_real_balance(callback: CallbackQuery, bot: Bot):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return

    try:
        star_amount = await bot.get_my_star_balance()
        tx_result = await bot.get_star_transactions(limit=10)

        lines = [
            f"💰 <b>Botning haqiqiy Stars balansi: {star_amount.amount} ⭐️</b>\n",
            "<b>So'nggi tranzaksiyalar:</b>",
        ]
        if tx_result.transactions:
            for tx in tx_result.transactions[:10]:
                dt = tx.date.strftime("%Y-%m-%d %H:%M")
                sign = "+" if tx.source else "-"
                lines.append(f"• {sign}{tx.amount} ⭐️ — {dt}")
        else:
            lines.append("— tranzaksiya yo'q —")

        lines.append(
            "\n⚠️ <b>Muhim:</b> bu balansni pulga aylantirish (Fragment orqali) Bot API'da "
            "mavjud emas — Telegram buni faqat botni yaratgan shaxsiy akkaunt orqali, "
            "2FA parol bilan, qo'lda amalga oshirishga ruxsat beradi.\n\n"
            "Yechish uchun: fragment.com'ga botingizni yaratgan Telegram akkaunt bilan kiring "
            "→ botingizni tanlang → \"Withdraw\" bo'limidan pulga aylantiring."
        )

        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Gift qilib o'zimga jo'natish", callback_data="admin_self_gift_withdraw")],
                [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")],
            ]),
        )
        await callback.answer()
    except Exception as e:
        log.exception("admin_real_balance xatoligi")
        try:
            await callback.answer(f"Xato: {str(e)[:180]}", show_alert=True)
        except Exception:
            pass


@router.callback_query(F.data == "admin_self_gift_withdraw")
async def cb_admin_self_gift_withdraw(callback: CallbackQuery, bot: Bot):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return

    try:
        star_amount = await bot.get_my_star_balance()
        gifts_result = await bot.get_available_gifts()

        # FAQAT belgilangan narxdagi gift (Army Teddy — granata ko'targan pluh
        # ayiqcha) ko'rsatiladi, boshqa barcha gift turlari chiqarib tashlanadi.
        # Sabab: bu gift turi keyinchalik NFT sifatida marketda oson sotiladi/
        # convert qilinadi, boshqalari esa faqat "dekorativ" bo'lib qolib ketishi
        # mumkin. Narxni ALLOWED_SELF_GIFT_STAR_COST orqali o'zgartirish mumkin.
        candidates = [g for g in gifts_result.gifts if g.star_count == ALLOWED_SELF_GIFT_STAR_COST]
        affordable = [g for g in candidates if g.star_count <= star_amount.amount]

        if not candidates:
            await callback.answer(
                f"{ALLOWED_SELF_GIFT_STAR_COST}⭐️lik ruxsat etilgan gift (Army Teddy) hozir "
                f"Telegram'da mavjud emas.",
                show_alert=True,
            )
            return
        if not affordable:
            await callback.answer(
                f"Bot balansi yetarli emas: {star_amount.amount}⭐️ (kerak: "
                f"{ALLOWED_SELF_GIFT_STAR_COST}⭐️).",
                show_alert=True,
            )
            return

        affordable.sort(key=lambda g: g.star_count)
        await callback.message.edit_text(
            f"🎁 <b>Gift tanlang</b> (bot balansi: {star_amount.amount} ⭐️):\n\n"
            f"<i>Faqat {ALLOWED_SELF_GIFT_STAR_COST}⭐️lik Army Teddy ruxsat etilgan. "
            f"Gift sizning shu (superadmin) akkauntingizga yuboriladi va bot balansidan "
            f"kamayadi.</i>",
            parse_mode="HTML",
            reply_markup=admin_self_gift_list_kb(affordable),
        )
        await callback.answer()
    except Exception as e:
        log.exception("admin_self_gift_withdraw xatoligi")
        try:
            await callback.answer(f"Xato: {str(e)[:180]}", show_alert=True)
        except Exception:
            pass


@router.callback_query(F.data.startswith("admin_self_gift_send:"))
async def cb_admin_self_gift_send(callback: CallbackQuery, bot: Bot):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    _, gift_id, price_s = callback.data.split(":")

    try:
        await bot.send_gift(user_id=callback.from_user.id, gift_id=gift_id)
    except TelegramBadRequest as e:
        await callback.answer(f"Gift yuborilmadi: {e}", show_alert=True)
        return
    except Exception as e:
        await callback.answer(f"Kutilmagan xato: {e}", show_alert=True)
        return

    await callback.message.edit_text(f"✅ Gift ({price_s} ⭐️) sizga yuborildi. Bot balansi kamaydi.")
    await callback.answer()


@router.callback_query(F.data == "admin_stars_settings")
async def cb_admin_stars_settings(callback: CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    amount = db.get_stars_per_unit()
    hours = db.get_seconds_per_unit() // 3600
    min_withdraw = db.get_min_withdraw_stars()
    block_hours = db.get_block_default_hours()
    await callback.message.edit_text(
        f"⭐️ <b>Stars narxi sozlamalari</b>\n\n"
        f"Hozirgi narx: <b>{amount} stars = {hours} soat</b> hosting.\n"
        f"Min. gift-yechish miqdori: <b>{min_withdraw} ⭐️</b>\n"
        f"Standart blok muddati (avval to'lagan userlar uchun): <b>{block_hours} soat</b>\n"
        f"Bu qiymatlar darhol o'zgaradi, redeploy shart emas.",
        parse_mode="HTML",
        reply_markup=admin_stars_settings_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_set_stars_amount")
async def cb_admin_set_stars_amount(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    await state.set_state(AdminStarsSetting.waiting_amount)
    await callback.message.answer(f"Yangi qiymat: nechta stars 1 birlik hosting narxi bo'lsin? Raqam yozing (hozir: {db.get_stars_per_unit()}).")
    await callback.answer()


@router.message(AdminStarsSetting.waiting_amount)
async def set_stars_amount(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Musbat butun son yuboring.")
        return
    db.set_setting("stars_per_unit", int(text))
    await message.answer(f"✅ Endi {text} stars = {db.get_seconds_per_unit() // 3600} soat hosting.")


@router.callback_query(F.data == "admin_set_stars_hours")
async def cb_admin_set_stars_hours(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    await state.set_state(AdminStarsSetting.waiting_hours)
    await callback.message.answer(f"Yangi muddat: necha soatlik hosting bo'lsin? Raqam yozing (hozir: {db.get_seconds_per_unit() // 3600}).")
    await callback.answer()


@router.message(AdminStarsSetting.waiting_hours)
async def set_stars_hours(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Musbat butun son yuboring.")
        return
    db.set_setting("seconds_per_unit", int(text) * 3600)
    await message.answer(f"✅ Endi {db.get_stars_per_unit()} stars = {text} soat hosting.")


@router.callback_query(F.data == "admin_set_min_withdraw")
async def cb_admin_set_min_withdraw(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    await state.set_state(AdminStarsSetting.waiting_min_withdraw)
    await callback.message.answer(f"Gift orqali yechish uchun minimal balans qancha bo'lsin? Raqam yozing (hozir: {db.get_min_withdraw_stars()}).")
    await callback.answer()


@router.message(AdminStarsSetting.waiting_min_withdraw)
async def set_min_withdraw(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Musbat butun son yuboring.")
        return
    db.set_setting("min_withdraw_stars", int(text))
    await message.answer(f"✅ Endi minimal yechish miqdori: {text} ⭐️")


@router.callback_query(F.data == "admin_set_block_hours")
async def cb_admin_set_block_hours(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    await state.set_state(AdminStarsSetting.waiting_block_hours)
    await callback.message.answer(
        f"Avval to'lagan foydalanuvchilar uchun standart blok muddati necha soat bo'lsin? "
        f"Raqam yozing (hozir: {db.get_block_default_hours()})."
    )
    await callback.answer()


@router.message(AdminStarsSetting.waiting_block_hours)
async def set_block_hours(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Musbat butun son yuboring.")
        return
    db.set_setting("block_default_hours", int(text))
    await message.answer(f"✅ Endi standart blok muddati: {text} soat.")


@router.callback_query(F.data == "admin_set_ai_help_price")
async def cb_admin_set_ai_help_price(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    await state.set_state(AdminStarsSetting.waiting_ai_help_price)
    await callback.message.answer(
        f"AI crash-tashxis so'rashning narxi necha ⭐️ bo'lsin? Raqam yozing (hozir: {db.get_ai_help_price_stars()})."
    )
    await callback.answer()


@router.message(AdminStarsSetting.waiting_ai_help_price)
async def set_ai_help_price(message: Message, state: FSMContext):
    await state.clear()
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Musbat butun son yuboring.")
        return
    db.set_setting("ai_help_price_stars", int(text))
    await message.answer(f"✅ Endi AI yordam narxi: {text} ⭐️")


@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    users = db.list_all_users()
    if not users:
        await callback.message.edit_text("Hozircha birorta ham foydalanuvchi yo'q.")
        await callback.answer()
        return
    await callback.message.edit_text(
        f"👥 <b>Foydalanuvchilar</b> (jami: {len(users)}):\n"
        f"✅ tasdiqlangan · ⏳ kutilmoqda · ⛔️ rad etilgan",
        parse_mode="HTML",
        reply_markup=admin_users_kb(users),
    )
    await callback.answer()


async def _build_user_view(telegram_id: int, viewer_id: int):
    """(text, reply_markup) qaytaradi — cb_admin_user_view, qidiruv natijasi (ID
    va QWERTY ikkalasi ham) va foydalanuvchilar ro'yxatidan bosilganda bir xil
    to'liq ma'lumot ko'rsatiladi: botlar (holati, tili, deploy sanasi, RAM),
    limit (nechtasi ishlatilgan/nechtasi bor), balans va to'lov tarixi."""
    user = db.get_user(telegram_id)
    if user is None:
        return None, None

    bots = db.list_user_bots(telegram_id)
    status_label = {"approved": "✅ Tasdiqlangan", "pending": "⏳ Kutilmoqda", "denied": "⛔️ Rad etilgan"}
    start_date = datetime.fromtimestamp(user["created_at"]).strftime("%Y-%m-%d %H:%M")

    effective_max_bots = user["max_bots"] if user["max_bots"] is not None else MAX_BOTS_PER_USER
    limit_note = " (individual)" if user["max_bots"] is not None else " (global)"
    topup_note = f" (umr bo'yi to'langan: {user['lifetime_topup_stars']})" if user["lifetime_topup_stars"] else ""

    if bots:
        bots_lines = []
        icon = {"running": "🟢", "crashed": "🟡", "stopped": "🔴"}
        status_word = {"running": "ishlayapti", "crashed": "qulagan", "stopped": "to'xtatilgan"}
        for b in bots:
            label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
            deployed = datetime.fromtimestamp(b["created_at"]).strftime("%Y-%m-%d")
            line = (
                f"{icon.get(b['status'], '❓')} <b>{html.escape(label)}</b> — "
                f"{status_word.get(b['status'], b['status'])}"
            )
            if b["language"]:
                line += f" · {html.escape(b['language'])}"
            line += f" · deploy: {deployed}"
            if b["status"] == "running" and is_running(b["bot_id"]):
                line += f" · {bot_ram_mb(b['bot_id']):.1f} MB"
            if b["stars_hosted"] and b["paid_until"]:
                paid_until_str = datetime.fromtimestamp(b["paid_until"]).strftime("%Y-%m-%d %H:%M")
                line += f"\n   ⭐️ Stars orqali, muddat: {paid_until_str}"
            bots_lines.append(line)
        bots_text = "\n".join(bots_lines)
    else:
        bots_text = "— hali bot deploy qilmagan —"

    text = (
        f"👤 <b>{html.escape(user['first_name'] or 'Nomsiz')}</b>"
        f"{' (@' + user['username'] + ')' if user['username'] else ''}\n"
        f"🆔 ID: <code>{user['telegram_id']}</code>\n"
        f"Holati: {status_label.get(user['status'], user['status'])}"
        f"{' — 🚫 RUXSATI OLIB TASHLANGAN' if user['is_banned'] else ''}\n"
        f"/start bosgan sana: {start_date}\n"
        f"⭐️ Balans: <b>{user['balance_stars']}</b> stars{topup_note}\n"
        f"📦 Limit: <b>{len(bots)}/{effective_max_bots}</b> bot{limit_note}\n\n"
        f"<b>Botlari ({len(bots)}):</b>\n{bots_text}"
    )
    kb = admin_user_view_kb(
        telegram_id, bots, current_max_bots=user["max_bots"],
        balance=user["balance_stars"], min_withdraw=db.get_min_withdraw_stars(),
        is_banned=bool(user["is_banned"]), paid_before=user["lifetime_topup_stars"] > 0,
        viewer_is_superadmin=is_superadmin(viewer_id),
    )
    return text, kb


@router.callback_query(F.data.startswith("admin_user_view:"))
async def cb_admin_user_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    text, kb = await _build_user_view(telegram_id, callback.from_user.id)
    if text is None:
        await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_search_choice")
async def cb_admin_search_choice(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    await callback.message.edit_text(
        "🔍 Qidiruv usulini tanlang:",
        reply_markup=admin_search_choice_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_search_by_id")
async def cb_admin_search_user_ask(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    await state.set_state(AdminSearchUser.waiting_query)
    await callback.message.answer("🔍 Telegram ID yoki username yuboring (masalan: 123456789 yoki @username):")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_search_qwerty_bs:"))
async def cb_admin_search_qwerty_backspace(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    current_query = callback.data.split(":", 1)[1]
    new_query = current_query[:-1]
    await _render_qwerty_search(callback, new_query)


@router.callback_query(F.data.startswith("admin_search_qwerty:"))
async def cb_admin_search_qwerty(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    query = callback.data.split(":", 1)[1]
    await _render_qwerty_search(callback, query)


async def _render_qwerty_search(callback: CallbackQuery, query: str):
    """admin_search_qwerty va admin_search_qwerty_bs uchun umumiy render logikasi —
    harf qo'shilganda ham, o'chirilganda ham bir xil: joriy so'rovni ko'rsatib,
    mos foydalanuvchilarni real vaqtda (har bosishda) filtrlab beradi."""
    matches = db.search_users_by_prefix(query) if query else []
    display_query = query if query else "—"
    match_count_note = f"({len(matches)} ta topildi)" if query else ""
    try:
        await callback.message.edit_text(
            f"🔤 Qidiruv: <code>{html.escape(display_query)}</code> {match_count_note}\n\n"
            f"Harflarni bosib so'zni yig'ing — mos foydalanuvchilar shu yerda darhol ko'rinadi.",
            parse_mode="HTML",
            reply_markup=admin_search_qwerty_kb(query, matches),
        )
    except Exception:
        pass  # matn o'zgarmagan bo'lsa Telegram xato qaytaradi (masalan bo'sh so'rovga qayta bosilganda)
    await callback.answer()


@router.message(AdminSearchUser.waiting_query)
async def admin_search_user_query(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    query = (message.text or "").strip()

    if query.lstrip("-").isdigit():
        user = db.get_user(int(query))
    else:
        user = db.get_user_by_username(query)

    if user is None:
        await message.answer("Foydalanuvchi topilmadi.")
        return

    text, kb = await _build_user_view(user["telegram_id"], message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def _apply_block(bot: Bot, admin_user, target_id: int, custom_hours=None) -> tuple[list, str]:
    """Botni bloklaydi, ishlab turgan botlarini to'xtatadi, kerak bo'lsa xabar/taymer
    qo'yadi va adminlarga audit xabari yuboradi. (stopped_bot_names, timer_text) qaytaradi."""
    user = db.get_user(target_id)
    db.set_user_banned(target_id, True)

    stopped = []
    for b in db.list_user_bots(target_id):
        if b["status"] == "running":
            stop_bot_process(b["bot_id"])
            db.set_bot_status(b["bot_id"], "stopped", None)
            stopped.append(b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}")

    paid_before = bool(user and user["lifetime_topup_stars"] > 0)

    if custom_hours is not None:
        # Admin aniq muddat belgilagan — toʻlov tarixidan qatʼi nazar shu qoʻllanadi.
        unblock_at = int(time.time()) + custom_hours * 3600
        db.set_user_blocked_until(target_id, unblock_at)
        try:
            await bot.send_message(
                target_id,
                f"⏸ Superadmin sizni vaqtincha bloklagan — botlaringiz to'xtatildi va hozircha "
                f"yangi bot host qila olmaysiz.\n\n{custom_hours} soatdan keyin avtomatik ochiladi.",
            )
        except Exception:
            pass
        timer_text = f" ({custom_hours} soatdan keyin avtomatik ochiladi)"
    elif paid_before:
        default_hours = db.get_block_default_hours()
        unblock_at = int(time.time()) + default_hours * 3600
        db.set_user_blocked_until(target_id, unblock_at)
        try:
            await bot.send_message(
                target_id,
                "⏸ Superadmin sizni vaqtincha bloklagan — botlaringiz to'xtatildi va hozircha "
                "yangi bot host qila olmaysiz.\n\n"
                f"Avval to'lov qilganingiz uchun {default_hours} soatdan keyin avtomatik ochiladi. Yoki "
                f"{db.get_unblock_min_stars()}+ ⭐️ to'lab darhol ochishingiz mumkin "
                f"({100 - db.get_unblock_fee_percent()}%'i balansingizga tushadi).",
            )
        except Exception:
            pass
        timer_text = f" ({default_hours} soatdan keyin avtomatik ochiladi)"
    else:
        # To'lamagan foydalanuvchi — bu shunchaki "vaqtincha to'xtatish" emas, BUTUNLAY
        # olib tashlanadi: status 'pending'ga qaytariladi va admin bergan limit
        # tozalanadi. Shu sabab keyinchalik "Host huquqini qaytarish" bosilsa ham,
        # eski huquqi AVTOMATIK qaytmaydi — qaytadan tasdiqlash yoki Stars kerak bo'ladi.
        db.set_user_blocked_until(target_id, None)
        db.set_user_status(target_id, "pending")
        db.set_user_max_bots(target_id, None)
        timer_text = " — BUTUNLAY olib tashlandi (qayta tasdiqlash yoki Stars kerak bo'ladi, avtomatik qaytmaydi)"

    for admin_id in set(ADMIN_IDS) | set(SUPERADMIN_IDS):
        if admin_id == admin_user.id:
            continue
        try:
            await bot.send_message(
                admin_id,
                f"ℹ️ {admin_user.first_name} foydalanuvchi <code>{target_id}</code>ning host "
                f"qilish huquqini vaqtincha olib tashladi.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await backup_database(bot)
    return stopped, timer_text


def _can_block_user(actor_id: int, target_id: int) -> tuple[bool, str]:
    """To'lagan (haqiqiy mijoz, lifetime_topup_stars>0) foydalanuvchini bloklash —
    faqat superadmin huquqi (jiddiyroq, biznesga ta'sir qiluvchi qaror).
    Hech qachon to'lamagan foydalanuvchini esa istalgan admin bloklay oladi."""
    if not is_admin(actor_id):
        return False, "Ruxsat yo'q."
    user = db.get_user(target_id)
    paid_before = bool(user and user["lifetime_topup_stars"] > 0)
    if paid_before and not is_superadmin(actor_id):
        return False, "Bu foydalanuvchi avval to'lov qilgan — uni bloklash faqat superadmin huquqi."
    return True, ""


@router.callback_query(F.data.startswith("admin_ban_ask:"))
async def cb_admin_ban_ask(callback: CallbackQuery):
    target_id = int(callback.data.split(":")[1])
    ok, reason = _can_block_user(callback.from_user.id, target_id)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return
    user = db.get_user(target_id)
    paid_before = bool(user and user["lifetime_topup_stars"] > 0)
    min_stars = db.get_unblock_min_stars()
    fee_pct = db.get_unblock_fee_percent()
    default_hours = db.get_block_default_hours()

    if paid_before:
        note = (
            f"Bu foydalanuvchi avval to'lov qilgan — standart tanlasangiz, avtomatik "
            f"<b>{default_hours} soatdan keyin</b> ochiladi, va botga xabar boradi. Yoki "
            f"{min_stars}+ ⭐️ to'lasa (bunda {100 - fee_pct}%'i balansiga tushadi, {fee_pct}%'i "
            f"xizmat haqi sifatida olinadi) — darhol ochiladi."
        )
    else:
        note = (
            "Bu foydalanuvchi hali to'lov qilmagan — standart tanlasangiz, host huquqi "
            "<b>BUTUNLAY</b> olib tashlanadi (status yangi/pending'ga qaytadi, admin bergan "
            "limit tozalanadi). \"Host huquqini qaytarish\" bosilsa ham eski huquqi avtomatik "
            "tiklanmaydi — qaytadan tasdiqlanishi yoki Stars orqali o'zi to'lashi kerak bo'ladi."
        )

    await callback.message.edit_text(
        f"🚫 <b>Host qilish huquqini vaqtincha olib tashlaysizmi?</b>\n\n"
        f"Bu foydalanuvchining barcha ishlab turgan botlari darhol to'xtatiladi, yangi bot host "
        f"qilish (admin tasdig'i yoki Stars orqali ham) bloklanadi — xuddi yangi (tasdiqlanmagan) "
        f"foydalanuvchi kabi bo'lib qoladi.\n\n{note}\n\n"
        f"Yoki pastdan maxsus muddat (soat) belgilashingiz mumkin — bu holda avtomatik "
        f"mantiq (to'lov tarixi) e'tiborga olinmaydi, siz kiritgan muddat qo'llanadi.",
        parse_mode="HTML",
        reply_markup=admin_ban_choice_kb(target_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ban_do:"))
async def cb_admin_ban_do(callback: CallbackQuery, bot: Bot):
    target_id = int(callback.data.split(":")[1])
    ok, reason = _can_block_user(callback.from_user.id, target_id)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return
    stopped, timer_text = await _apply_block(bot, callback.from_user, target_id)
    stopped_text = f"\nTo'xtatilgan botlar: {', '.join(stopped)}" if stopped else ""
    await callback.message.edit_text(f"✅ Host qilish huquqi olib tashlandi{timer_text}.{stopped_text}")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ban_custom_hours:"))
async def cb_admin_ban_custom_hours(callback: CallbackQuery, state: FSMContext):
    target_id = int(callback.data.split(":")[1])
    ok, reason = _can_block_user(callback.from_user.id, target_id)
    if not ok:
        await callback.answer(reason, show_alert=True)
        return
    await state.update_data(ban_target_id=target_id)
    await state.set_state(AdminBanCustomHours.waiting_hours)
    await callback.message.answer("Necha soatga bloklaysiz? Raqam yozing (masalan: 6, 48, 72).")
    await callback.answer()


@router.message(AdminBanCustomHours.waiting_hours)
async def ban_custom_hours_entered(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data.get("ban_target_id")
    await state.clear()

    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat butun son yuboring (soat sifatida).")
        return

    hours = int(text)
    ok, reason = _can_block_user(message.from_user.id, target_id)
    if not ok:
        await message.answer(reason)
        return
    stopped, timer_text = await _apply_block(bot, message.from_user, target_id, custom_hours=hours)
    stopped_text = f"\nTo'xtatilgan botlar: {', '.join(stopped)}" if stopped else ""
    await message.answer(f"✅ Host qilish huquqi olib tashlandi{timer_text}.{stopped_text}")


@router.callback_query(F.data.startswith("admin_unban:"))
async def cb_admin_unban(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(callback.data.split(":")[1])
    db.set_user_banned(target_id, False)
    db.set_user_blocked_until(target_id, None)
    await backup_database(bot)
    await callback.answer("✅ Ruxsat qaytarildi.", show_alert=True)
    # Ro'yxatni yangilab ko'rsatamiz
    user = db.get_user(target_id)
    bots = db.list_user_bots(target_id)
    await callback.message.edit_reply_markup(
        reply_markup=admin_user_view_kb(
            target_id, bots, current_max_bots=user["max_bots"],
            balance=user["balance_stars"], min_withdraw=db.get_min_withdraw_stars(),
            is_banned=False, paid_before=user["lifetime_topup_stars"] > 0,
            viewer_is_superadmin=is_superadmin(callback.from_user.id),
        ),
    )


@router.callback_query(F.data.startswith("admin_gift_withdraw:"))
async def cb_admin_gift_withdraw(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(callback.data.split(":")[1])
    user = db.get_user(target_id)
    if user is None:
        await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return

    try:
        gifts_result = await bot.get_available_gifts()
    except Exception as e:
        await callback.answer(f"Gift ro'yxatini olishda xato: {e}", show_alert=True)
        return

    affordable = [g for g in gifts_result.gifts if g.star_count <= user["balance_stars"]]
    if not affordable:
        await callback.answer(
            f"Balansga ({user['balance_stars']} ⭐️) mos keladigan gift topilmadi — "
            f"eng arzoni ham qimmatroq.",
            show_alert=True,
        )
        return

    affordable.sort(key=lambda g: g.star_count)
    await callback.message.edit_text(
        f"🎁 <b>Gift tanlang</b> (foydalanuvchi balansi: {user['balance_stars']} ⭐️):\n\n"
        f"<i>Diqqat: gift bot'ning HAQIQIY Stars balansidan yuboriladi (foydalanuvchi to'lovlaridan "
        f"to'plangan real balans). Bot balansi yetmasa, yuborish muvaffaqiyatsiz bo'ladi.</i>",
        parse_mode="HTML",
        reply_markup=admin_gift_list_kb(target_id, affordable),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_gift_send:"))
async def cb_admin_gift_send(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    _, target_id_s, gift_id, price_s = callback.data.split(":")
    target_id = int(target_id_s)
    price = int(price_s)

    user = db.get_user(target_id)
    if user is None or user["balance_stars"] < price:
        await callback.answer("Balans o'zgargan (kamayib ketgan) — qaytadan urinib ko'ring.", show_alert=True)
        return

    try:
        await bot.send_gift(user_id=target_id, gift_id=gift_id)
    except TelegramBadRequest as e:
        await callback.answer(f"Gift yuborilmadi: {e}", show_alert=True)
        return
    except Exception as e:
        await callback.answer(f"Kutilmagan xato: {e}", show_alert=True)
        return

    db.add_user_balance(target_id, -price, reason=f"Gift orqali yechish ({price}⭐️)")
    new_balance = db.get_user_balance(target_id)

    try:
        await bot.send_message(target_id, f"🎁 Sizga admin tomonidan gift yuborildi! Balansingizdan {price} ⭐️ yechildi.")
    except Exception:
        pass

    await callback.message.edit_text(
        f"✅ Gift yuborildi ({price} ⭐️). Foydalanuvchining yangi balansi: {new_balance} ⭐️",
        reply_markup=admin_user_view_kb(
            target_id, db.list_user_bots(target_id), current_max_bots=user["max_bots"],
            balance=new_balance, min_withdraw=db.get_min_withdraw_stars(),
            is_banned=bool(user["is_banned"]), paid_before=user["lifetime_topup_stars"] > 0,
            viewer_is_superadmin=is_superadmin(callback.from_user.id),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_all_bots")
async def cb_admin_all_bots(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    bots = db.list_all_bots()
    if not bots:
        await callback.message.edit_text("Hozircha birorta ham bot deploy qilinmagan.")
        await callback.answer()
        return
    await callback.message.edit_text("Barcha deploy qilingan botlar:", reply_markup=admin_all_bots_kb(bots))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_bot_view:"))
async def cb_admin_bot_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if bot_row is None:
        await callback.answer("Bot topilmadi.", show_alert=True)
        return

    text = (
        f"🤖 <b>{html.escape(bot_row['bot_username'] or bot_row['display_name'] or 'Nomsiz bot')}</b>\n"
        f"Holati: {'🟢 ishlayapti' if bot_row['status'] == 'running' else '🔴 to‘xtatilgan'}\n"
        f"Owner ID: <code>{bot_row['owner_id']}</code>\n"
        f"Til: {html.escape(bot_row['language'] or '-')}"
    )

    # Admin uchun: kod ichidan avtomatik aniqlangan token/chat_id'lar, fayl+qator bilan
    if bot_row["detected_credentials"]:
        try:
            creds = json.loads(bot_row["detected_credentials"])
        except Exception:
            creds = []
        if creds:
            text += "\n\n🔍 <b>Kod ichidan aniqlangan (taxminiy):</b>"
            for c in creds[:15]:
                if c["type"] == "token":
                    text += f"\n• Token: <code>{html.escape(c['value'])}</code> — {html.escape(c['file'])}:{c['line']}"
                else:
                    text += (
                        f"\n• {html.escape(c.get('var_name', 'ID'))}: <code>{html.escape(c['value'])}</code> "
                        f"— {html.escape(c['file'])}:{c['line']}"
                    )
            if len(creds) > 15:
                text += f"\n… va yana {len(creds) - 15} ta"

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_bot_view_kb(bot_row))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_owner_info:"))
async def cb_admin_owner_info(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if bot_row is None:
        await callback.answer("Bot topilmadi.", show_alert=True)
        return

    owner = db.get_user(bot_row["owner_id"])
    owner_bots_count = db.count_user_bots(bot_row["owner_id"])
    start_date = (
        datetime.fromtimestamp(owner["created_at"]).strftime("%Y-%m-%d %H:%M") if owner else "noma'lum"
    )

    text = (
        f"👤 <b>Foydalanuvchi haqida</b>\n\n"
        f"🆔 ID: <code>{owner['telegram_id']}</code>\n"
        f"Username: @{owner['username'] if owner and owner['username'] else '-'}\n"
        f"Ism: {html.escape(owner['first_name']) if owner and owner['first_name'] else '-'}\n"
        f"/start bosgan sana: {start_date}\n"
        f"Deploy qilingan botlar soni: {owner_bots_count}"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML", reply_markup=owner_info_kb(bot_row["owner_id"], bot_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_msg_user:"))
async def cb_admin_msg_user(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(callback.data.split(":")[1])
    await state.update_data(msg_target_id=target_id)
    await state.set_state(AdminMessageUser.waiting_text)
    await callback.message.answer("Foydalanuvchiga yubormoqchi bo'lgan xabaringizni yozing:")
    await callback.answer()


@router.message(AdminMessageUser.waiting_text)
async def admin_msg_text_entered(message: Message, state: FSMContext):
    await state.update_data(msg_text=message.text)

    if is_superadmin(message.from_user.id):
        await message.answer(
            "Xabar kim nomidan yuborilsin?",
            reply_markup=admin_sender_choice_kb("admin_msg_sender"),
        )
        return

    await _send_admin_msg(message, state, sender="admin")


async def _send_admin_msg(message: Message, state: FSMContext, sender: str):
    data = await state.get_data()
    target_id = data.get("msg_target_id")
    text = data.get("msg_text")
    await state.clear()
    try:
        await message.bot.send_message(target_id, f"{_sender_label(sender)}{html.escape(text)}", parse_mode="HTML")
        await message.answer("Xabar yuborildi ✅")
    except Exception:
        await message.answer("Xabar yuborilmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).")


@router.callback_query(F.data.startswith("admin_msg_sender:"))
async def cb_admin_msg_sender(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    sender = callback.data.split(":")[1]
    await callback.answer()
    await _send_admin_msg(callback.message, state, sender=sender)


@router.callback_query(F.data.startswith("admin_set_limit:"))
async def cb_admin_set_limit(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(callback.data.split(":")[1])
    await state.update_data(limit_target_id=target_id)
    await state.set_state(AdminSetLimit.waiting_number)
    await callback.message.answer(
        "Bu foydalanuvchi uchun nechta bot host qilishga ruxsat berasiz? Raqam yozing.\n"
        "0 yozsangiz — global standart limitga qaytaradi (default sozlamaga)."
    )
    await callback.answer()


@router.message(AdminSetLimit.waiting_number)
async def set_limit_number(message: Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("limit_target_id")
    await state.clear()

    text = (message.text or "").strip()
    if not text.lstrip("-").isdigit():
        await message.answer("Iltimos, faqat raqam yuboring (masalan: 5).")
        return

    n = int(text)
    if n <= 0:
        db.set_user_max_bots(target_id, None)
        await message.answer("✅ Bu foydalanuvchi uchun global standart limit qaytarildi.")
    else:
        db.set_user_max_bots(target_id, n)
        await message.answer(f"✅ Bu foydalanuvchi uchun limit endi: {n} ta bot.")


@router.callback_query(F.data == "admin_add_bot")
async def cb_admin_add_bot(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    # add_bot.py dagi asosiy oqimni qayta ishlatamiz
    from handlers.add_bot import ONLY_PYTHON_TEXT
    from states import AddBot
    from keyboards import cancel_kb

    await state.clear()
    await state.set_state(AddBot.waiting_code)
    await callback.message.answer(
        ONLY_PYTHON_TEXT + "\n\nBotingiz kodini <b>.py</b> fayl yoki <b>.zip</b> arxiv ko'rinishida yuboring.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_broadcast_ask")
async def cb_admin_broadcast_ask(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    await state.set_state(AdminBroadcast.waiting_text)
    await callback.message.answer(
        "📢 Barcha ro'yxatdan o'tgan foydalanuvchilarga yubormoqchi bo'lgan xabaringizni yozing "
        "(HTML formatlash qo'llab-quvvatlanadi: <b>qalin</b>, <i>egik</i> va h.k.):"
    )
    await callback.answer()


def _sender_label(choice: str) -> str:
    return "👑 <b>Egadan xabar:</b>\n\n" if choice == "owner" else "👤 <b>Admindan xabar:</b>\n\n"


@router.message(AdminBroadcast.waiting_text)
async def admin_broadcast_text_entered(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(broadcast_text=message.html_text)

    if is_superadmin(message.from_user.id):
        await message.answer(
            "Xabar kim nomidan yuborilsin?",
            reply_markup=admin_sender_choice_kb("admin_broadcast_sender"),
        )
        return

    await state.update_data(broadcast_sender="admin")
    await _show_broadcast_preview(message, state)


async def _show_broadcast_preview(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    sender = data.get("broadcast_sender", "admin")
    users = db.list_all_users()
    await message.answer(
        f"<b>Oldindan ko'rish</b> ({'👑 Ega' if sender == 'owner' else '👤 Admin'} nomidan):\n\n"
        f"{_sender_label(sender)}{text}\n\n"
        f"👥 Jami <b>{len(users)}</b> ta foydalanuvchiga yuboriladi. Davom etamizmi?",
        parse_mode="HTML",
        reply_markup=admin_broadcast_confirm_kb(),
    )


@router.callback_query(F.data.startswith("admin_broadcast_sender:"))
async def cb_admin_broadcast_sender(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    sender = callback.data.split(":")[1]
    await state.update_data(broadcast_sender=sender)
    await callback.answer()
    await _show_broadcast_preview(callback.message, state)


@router.callback_query(F.data == "admin_broadcast_confirm")
async def cb_admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    sender = data.get("broadcast_sender", "admin")
    await state.clear()
    if not text:
        await callback.answer("Xabar matni topilmadi, qaytadan urinib ko'ring.", show_alert=True)
        return

    full_text = _sender_label(sender) + text
    await callback.answer()
    await callback.message.edit_text("📤 Yuborilmoqda...")

    users = db.list_all_users()
    sent, failed = 0, 0
    for user_row in users:
        try:
            await bot.send_message(user_row["telegram_id"], full_text, parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Telegram flood limitidan (~30 xabar/sek) saqlanish uchun

    await callback.message.answer(
        f"✅ Broadcast tugadi.\nYuborildi: {sent}\nYuborilmadi (bloklagan/xato): {failed}"
    )


@router.callback_query(F.data.startswith("admin_test_deploy:"))
async def cb_admin_test_deploy_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if bot_row is None:
        await callback.answer("Bot topilmadi.", show_alert=True)
        return

    await state.update_data(test_source_bot_id=bot_id)
    await state.set_state(AdminTestDeploy.waiting_token)
    await callback.message.answer(
        "🧪 <b>Shaxsiy test-deploy</b>\n\n"
        "Bu bot kodining nusxasini SIZNING o'zingiz nomidan, alohida bot sifatida "
        "deploy qiladi (asl botga tegmaydi).\n\n"
        "O'zingizning bot tokeningizni yuboring (@BotFather'dan olingan):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminTestDeploy.waiting_token)
async def test_deploy_token_entered(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not re.match(r'^\d{8,10}:[A-Za-z0-9_-]{35}$', text):
        await message.answer("Bu to'g'ri token ko'rinishida emas. Qaytadan yuboring (masalan: 123456789:ABC-...).")
        return

    await state.update_data(test_new_token=text)
    data = await state.get_data()
    source_bot_id = data.get("test_source_bot_id")
    source_row = db.get_bot(source_bot_id)

    creds = []
    if source_row and source_row["detected_credentials"]:
        try:
            creds = json.loads(source_row["detected_credentials"])
        except Exception:
            creds = []
    chat_id_hit = next((c for c in creds if c["type"] == "chat_id"), None)

    if chat_id_hit:
        await state.set_state(AdminTestDeploy.waiting_chat_id)
        await message.answer(
            f"Kodda <code>{html.escape(chat_id_hit.get('var_name', 'ID'))} = {chat_id_hit['value']}</code> "
            f"topildi ({chat_id_hit['file']}:{chat_id_hit['line']}).\n\n"
            f"Buni ham almashtirmoqchimisiz? Yangi ID raqamini yuboring, yoki o'zgartirmaslik uchun "
            f"<code>skip</code> deb yozing.",
            parse_mode="HTML",
        )
        return

    await _finalize_test_deploy(message, state)


@router.message(AdminTestDeploy.waiting_chat_id)
async def test_deploy_chat_id_entered(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() != "skip":
        if not re.match(r'^-?\d{6,15}$', text):
            await message.answer("Bu ID ko'rinishida emas. Raqam yuboring yoki 'skip' deb yozing.")
            return
        await state.update_data(test_new_chat_id=text)
    await _finalize_test_deploy(message, state)


async def _finalize_test_deploy(message: Message, state: FSMContext):
    data = await state.get_data()
    source_bot_id = data.get("test_source_bot_id")
    new_token = data.get("test_new_token")
    new_chat_id = data.get("test_new_chat_id")
    await state.clear()

    source_row = db.get_bot(source_bot_id)
    if source_row is None:
        await message.answer("Manba bot topilmadi.")
        return

    allowed, used_mb, budget_mb = can_start_new_bot()
    if not allowed:
        await message.answer(f"⚠️ Server RAM byudjeti tugagan ({used_mb:.0f}/{budget_mb} MB). Hozircha test-deploy qilib bo'lmaydi.")
        return

    await message.answer("🧪 Test-deploy tayyorlanmoqda...")

    try:
        new_bot_id = db.create_bot(
            owner_id=message.from_user.id, bot_username=None, bot_token=new_token,
            code_path=source_row["code_path"],  # vaqtincha, pastda yangilanadi
            storage_file_id=None, is_zip=bool(source_row["is_zip"]), language=source_row["language"],
            build_cmd=source_row["build_cmd"], start_cmd=source_row["start_cmd"],
            display_name=f"{source_row['bot_username'] or source_row['display_name'] or 'bot'} (TEST)",
        )
        db.mark_bot_test_clone(new_bot_id)

        new_workdir = bot_workdir(new_bot_id)
        await asyncio.to_thread(shutil.copytree, source_row["code_path"], new_workdir, dirs_exist_ok=True)
        db.set_bot_code_path(new_bot_id, new_workdir)

        # Aniqlangan eski token/chat_id'larni yangilari bilan almashtiramiz (matn almashtirish)
        creds = []
        if source_row["detected_credentials"]:
            try:
                creds = json.loads(source_row["detected_credentials"])
            except Exception:
                creds = []
        replacements = {}
        old_token_hit = next((c for c in creds if c["type"] == "token"), None)
        if old_token_hit:
            replacements[old_token_hit["value"]] = new_token
        if new_chat_id:
            old_chat_hit = next((c for c in creds if c["type"] == "chat_id"), None)
            if old_chat_hit:
                replacements[old_chat_hit["value"]] = new_chat_id

        for root, _, files in os.walk(new_workdir):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    changed = False
                    for old_val, new_val in replacements.items():
                        if old_val in content:
                            content = content.replace(old_val, new_val)
                            changed = True
                    if changed:
                        with open(fpath, "w", encoding="utf-8") as f:
                            f.write(content)
                except Exception:
                    continue

        log_file = open(os.path.join(new_workdir, "run.log"), "w", encoding="utf-8")
        build_ok = await asyncio.to_thread(run_build_command, new_workdir, source_row["build_cmd"] or "", log_file)
        log_file.close()
        if not build_ok:
            db.set_bot_status(new_bot_id, "crashed", None)
            crash_log = read_log_tail(new_workdir, n_lines=40)
            await message.answer(f"❌ Build muvaffaqiyatsiz:\n<pre>{html.escape(crash_log[-2500:])}</pre>", parse_mode="HTML")
            return

        pid = start_bot_process(new_bot_id, new_workdir, source_row["start_cmd"], {})
        db.set_bot_status(new_bot_id, "running", pid)
        await asyncio.sleep(3)
        if not is_running(new_bot_id):
            db.set_bot_status(new_bot_id, "crashed", None)
            crash_log = read_log_tail(new_workdir, n_lines=40)
            await message.answer(f"❌ Ishga tushgach qulab tushdi:\n<pre>{html.escape(crash_log[-2500:])}</pre>", parse_mode="HTML")
            return

        try:
            temp_bot = Bot(token=new_token)
            me = await temp_bot.get_me()
            db.set_bot_username(new_bot_id, me.username)
            await temp_bot.session.close()
            username_text = f"\n🤖 @{me.username}"
        except Exception:
            username_text = ""

        await message.answer(
            f"✅ <b>Test-deploy tayyor va ishlab turibdi!</b>{username_text}\n\n"
            f"Bu asl botdan mustaqil nusxa — \"Mening botlarim\"dan boshqarasiz. "
            f"Diqqat: bu nusxa Render qayta ko'tarilganda avtomatik tiklanmaydi "
            f"(faqat test uchun, doimiy backup mexanizmiga ulanmagan).",
            parse_mode="HTML",
        )
    except Exception as e:
        log.exception("Test-deploy xatoligi")
        await message.answer(f"Xato: {str(e)[:300]}")


# ---------- AI provayderlar (superadmin: bir nechta AI API qo'shish, byudjet muammosini yumshatish) ----------

@router.callback_query(F.data == "admin_ai_providers")
async def cb_admin_ai_providers(callback: CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    providers = db.list_ai_providers()
    if not providers:
        await callback.message.edit_text(
            "🤖 <b>AI provayderlar</b>\n\nHozircha birorta ham qo'shilmagan. "
            "Foydalanuvchilar \"AI yordam\" tugmasini ko'rmaydi (yoki bosganda xato oladi), "
            "toki kamida bitta faol provayder qo'shilmaguncha.",
            parse_mode="HTML",
            reply_markup=admin_ai_providers_kb([]),
        )
        await callback.answer()
        return

    lines = ["🤖 <b>AI provayderlar</b>\n"]
    for p in providers:
        used_today = db.get_ai_provider_usage_today(p["provider_id"])
        limit_label = f"{used_today}/{p['daily_limit']}" if p["daily_limit"] > 0 else f"{used_today}/cheksiz"
        status = "🟢 faol" if p["is_active"] else "⚪️ nofaol"
        lines.append(f"• <b>{html.escape(p['name'])}</b> ({status}) — bugun: {limit_label} so'rov")
    lines.append("\nBatafsil ko'rish uchun ro'yxatdan tanlang:")

    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_ai_providers_kb(providers))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ai_provider_view:"))
async def cb_admin_ai_provider_view(callback: CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    provider_id = int(callback.data.split(":")[1])
    provider = db.get_ai_provider(provider_id)
    if provider is None:
        await callback.answer("Provayder topilmadi.", show_alert=True)
        return

    used_today = db.get_ai_provider_usage_today(provider_id)
    limit_label = f"{used_today}/{provider['daily_limit']}" if provider["daily_limit"] > 0 else f"{used_today}/cheksiz"
    masked_key = (provider["api_key"][:6] + "..." + provider["api_key"][-4:]) if provider["api_key"] and len(provider["api_key"]) > 12 else ("bor" if provider["api_key"] else "yo'q")

    text = (
        f"🤖 <b>{html.escape(provider['name'])}</b>\n\n"
        f"Endpoint: <code>{html.escape(provider['base_url'])}</code>\n"
        f"Model: <code>{html.escape(provider['model'])}</code>\n"
        f"API key: <code>{html.escape(masked_key)}</code>\n"
        f"Ustuvorlik: {provider['priority']} (kichikroq = avval sinaladi)\n"
        f"Kunlik limit: {limit_label} so'rov\n"
        f"Holati: {'🟢 faol' if provider['is_active'] else '⚪️ nofaol'}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_ai_provider_view_kb(provider))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_ai_provider_toggle:"))
async def cb_admin_ai_provider_toggle(callback: CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    provider_id = int(callback.data.split(":")[1])
    provider = db.get_ai_provider(provider_id)
    if provider is None:
        await callback.answer("Provayder topilmadi.", show_alert=True)
        return
    db.set_ai_provider_active(provider_id, not provider["is_active"])
    await cb_admin_ai_provider_view(callback)


@router.callback_query(F.data.startswith("admin_ai_provider_delete:"))
async def cb_admin_ai_provider_delete(callback: CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    provider_id = int(callback.data.split(":")[1])
    db.delete_ai_provider(provider_id)
    await callback.answer("🗑 Provayder o'chirildi.", show_alert=True)
    await cb_admin_ai_providers(callback)


@router.callback_query(F.data == "admin_ai_provider_add")
async def cb_admin_ai_provider_add(callback: CallbackQuery, state: FSMContext):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return
    await state.set_state(AdminAIProvider.waiting_kind)
    await callback.message.answer(
        "🤖 Yangi AI provayder qo'shish.\n\n"
        "Cloudflare Workers AI bo'lsa, faqat Account ID va API kalit yetarli — "
        "endpoint manzilini o'zim yasab beraman. Boshqa xizmat (OpenRouter, Groq va h.k.) "
        "bo'lsa, to'liq endpoint URL kerak bo'ladi.",
        reply_markup=admin_ai_provider_kind_kb(),
    )
    await callback.answer()


@router.callback_query(AdminAIProvider.waiting_kind, F.data.startswith("admin_ai_kind:"))
async def ai_provider_kind_chosen(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(":", 1)[1]
    await state.update_data(ai_kind=kind)
    default_name = "Cloudflare Workers AI" if kind == "cloudflare" else ""
    await state.update_data(ai_name=default_name)
    await state.set_state(AdminAIProvider.waiting_name)

    if kind == "cloudflare":
        await callback.message.answer(
            f"Nom sifatida <b>{default_name}</b> ishlatilsin, yoki o'zingiz nom yozing "
            f"(masalan agar bir nechta Cloudflare hisobingiz bo'lsa, ularni farqlash uchun):",
            parse_mode="HTML",
        )
    else:
        await callback.message.answer("Provayder nomini yozing (masalan: <code>OpenRouter</code>):", parse_mode="HTML")
    await callback.answer()


@router.message(AdminAIProvider.waiting_name)
async def ai_provider_name_entered(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Nom bo'sh bo'lishi mumkin emas.")
        return
    await state.update_data(ai_name=name)
    data = await state.get_data()

    if data.get("ai_kind") == "cloudflare":
        await state.set_state(AdminAIProvider.waiting_account_id)
        await message.answer(
            "☁️ Cloudflare <b>Account ID</b>'ni yuboring.\n\n"
            "Qayerdan olish mumkin: dash.cloudflare.com → istalgan sayt yoki "
            "\"Workers &amp; Pages\" bo'limi → o'ng tarafdagi panelda \"Account ID\" "
            "sifatida ko'rinadi (32 belgili kod, masalan: <code>a1b2c3d4e5f6...</code>).",
            parse_mode="HTML",
        )
    else:
        await state.set_state(AdminAIProvider.waiting_base_url)
        await message.answer(
            "Endpoint URL'ni yozing (OpenAI-compatible, <code>/chat/completions</code>gacha, masalan:\n"
            "<code>https://openrouter.ai/api/v1</code>):",
            parse_mode="HTML",
        )


@router.message(AdminAIProvider.waiting_account_id)
async def ai_provider_account_id_entered(message: Message, state: FSMContext):
    account_id = (message.text or "").strip()
    if not account_id or " " in account_id:
        await message.answer("To'g'ri Account ID yuboring (bo'sh joysiz, bitta so'z sifatida).")
        return
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
    await state.update_data(ai_base_url=base_url)
    await state.set_state(AdminAIProvider.waiting_api_key)
    await message.answer(
        f"✅ Endpoint tayyor: <code>{html.escape(base_url)}</code>\n\n"
        f"Endi API kalitni yuboring — bu dash.cloudflare.com → My Profile → API Tokens → "
        f"\"Create Token\" → \"Workers AI\" shablonidan olinadi (bazada shifrlangan holda saqlanadi):",
        parse_mode="HTML",
    )


@router.message(AdminAIProvider.waiting_base_url)
async def ai_provider_url_entered(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("http"):
        await message.answer("To'g'ri URL yozing (http:// yoki https:// bilan boshlanishi kerak).")
        return
    await state.update_data(ai_base_url=url)
    await state.set_state(AdminAIProvider.waiting_api_key)
    await message.answer(
        "API kalitni yuboring (bu bazada shifrlangan holda saqlanadi). "
        "Agar kalit shart bo'lmasa (masalan lokal endpoint), <code>-</code> yozing:",
        parse_mode="HTML",
    )


@router.message(AdminAIProvider.waiting_api_key)
async def ai_provider_key_entered(message: Message, state: FSMContext):
    key = (message.text or "").strip()
    await state.update_data(ai_api_key=None if key == "-" else key)
    data = await state.get_data()

    if data.get("ai_kind") == "cloudflare":
        await state.set_state(AdminAIProvider.waiting_model)
        await message.answer(
            "Endi model tanlang (kodlash uchun mos variantlar tayyorlab qo'ydim):",
            reply_markup=admin_ai_cloudflare_model_kb(),
        )
    else:
        await state.set_state(AdminAIProvider.waiting_model)
        await message.answer(
            "Model nomini yozing (masalan: <code>gpt-4o-mini</code> yoki xizmatingiz taqdim etgan model ID'si):",
            parse_mode="HTML",
        )


@router.callback_query(AdminAIProvider.waiting_model, F.data.startswith("admin_ai_cf_model:"))
async def ai_provider_cf_model_chosen(callback: CallbackQuery, state: FSMContext):
    model = callback.data.split(":", 1)[1]
    if model == "custom":
        await callback.message.answer("Model nomini o'zingiz yozing (masalan: <code>@cf/...</code>):", parse_mode="HTML")
        await callback.answer()
        return  # AdminAIProvider.waiting_model holatida qolamiz — matn handler'i kutadi

    await state.update_data(ai_model=model)
    await state.set_state(AdminAIProvider.waiting_daily_limit)
    await callback.message.answer(
        "Kunlik so'rov limiti nechta bo'lsin? (0 = cheklovsiz — Cloudflare dashboard'idagi "
        "haqiqiy Neuron sarfingizga qarab belgilang, masalan: 30):"
    )
    await callback.answer()


@router.message(AdminAIProvider.waiting_model)
async def ai_provider_model_entered(message: Message, state: FSMContext):
    model = (message.text or "").strip()
    if not model:
        await message.answer("Model nomi bo'sh bo'lishi mumkin emas.")
        return
    await state.update_data(ai_model=model)
    await state.set_state(AdminAIProvider.waiting_daily_limit)
    await message.answer(
        "Kunlik so'rov limiti nechta bo'lsin? (0 = cheklovsiz — Cloudflare dashboard'idagi "
        "haqiqiy Neuron sarfingizga qarab belgilang, masalan: 40):"
    )


@router.message(AdminAIProvider.waiting_daily_limit)
async def ai_provider_limit_entered(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Butun son yuboring (0 = cheklovsiz).")
        return
    await state.update_data(ai_daily_limit=int(text))
    await state.set_state(AdminAIProvider.waiting_price_stars)
    await message.answer(
        "Ustuvorlik darajasini yozing (kichikroq raqam = avval sinaladi, masalan: 10):"
    )


@router.message(AdminAIProvider.waiting_price_stars)
async def ai_provider_priority_entered(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Butun son yuboring.")
        return
    data = await state.get_data()
    await state.clear()

    provider_id = db.create_ai_provider(
        name=data["ai_name"],
        base_url=data["ai_base_url"],
        api_key=data.get("ai_api_key"),
        model=data["ai_model"],
        daily_limit=data["ai_daily_limit"],
        priority=int(text),
    )
    await message.answer(
        f"✅ AI provayder qo'shildi: <b>{html.escape(data['ai_name'])}</b> (ID: {provider_id}).\n"
        f"Endi u faol holatda — \"🤖 AI provayderlar\" bo'limidan boshqarishingiz mumkin.",
        parse_mode="HTML",
    )


# ---------- Superadmin dashboard: umumiy statistika ----------

@router.callback_query(F.data == "admin_dashboard")
async def cb_admin_dashboard(callback: CallbackQuery):
    if not is_superadmin(callback.from_user.id):
        await callback.answer("Bu faqat superadminlar uchun.", show_alert=True)
        return

    stats = db.get_dashboard_stats()

    # Eng ko'p RAM yeyotgan botlarni real vaqtda hisoblab, top-5'ni chiqaramiz —
    # bu ma'lumot bazada saqlanmaydi, faqat hozirgi jonli jarayonlardan olinadi.
    ram_usage = []
    for row in stats["running_bot_rows"]:
        ram_mb = bot_ram_mb(row["bot_id"])
        if ram_mb > 0:
            label = row["bot_username"] or row["display_name"] or f"Bot #{row['bot_id']}"
            ram_usage.append((label, ram_mb))
    ram_usage.sort(key=lambda x: x[1], reverse=True)
    top_ram = ram_usage[:5]

    allowed, used_mb, budget_mb = can_start_new_bot()

    lines = [
        "📊 <b>Umumiy statistika</b>\n",
        f"👥 Foydalanuvchilar: <b>{stats['total_users']}</b> "
        f"(✅ {stats['approved_users']} tasdiqlangan, ⏳ {stats['pending_users']} kutmoqda, "
        f"⛔️ {stats['banned_users']} ban qilingan)",
        f"🆕 Bugun ro'yxatdan o'tgan: {stats['new_users_today']}",
        "",
        f"🤖 Botlar: <b>{stats['total_bots']}</b> jami "
        f"(🟢 {stats['running_bots']} ishlayapti, 🟡 {stats['crashed_bots']} qulagan, "
        f"⭐️ {stats['stars_hosted_bots']} Stars orqali)",
        f"🚀 Deploy: bugun {stats['deploys_today']}, oxirgi 7 kunda {stats['deploys_week']}",
        "",
        f"💾 Server RAM: {used_mb:.0f}/{budget_mb:.0f} MB band",
        f"💰 Umr bo'yi to'langan Stars: {stats['total_topup_stars']}",
        f"🤖 Bugungi AI so'rovlari: {stats['ai_calls_today']}",
    ]
    if top_ram:
        lines.append("\n💾 <b>Eng ko'p RAM yeyotgan botlar:</b>")
        for label, ram_mb in top_ram:
            lines.append(f"  • {html.escape(label)} — {ram_mb:.1f} MB")

    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Yangilash", callback_data="admin_dashboard")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_panel_back")],
        ]),
    )
    await callback.answer()
