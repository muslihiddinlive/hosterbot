import html
import time
import logging
import asyncio
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin, is_superadmin, STORAGE_GROUP_ID, ADMIN_IDS, SUPERADMIN_IDS
from states import AdminMessageUser, AdminSetLimit, AdminStarsSetting, AdminBanCustomHours, AdminBroadcast
from keyboards import admin_all_bots_kb, admin_bot_view_kb, owner_info_kb, admin_users_kb, admin_user_view_kb, admin_panel_kb, admin_stars_settings_kb, admin_gift_list_kb, admin_self_gift_list_kb, admin_ban_choice_kb, admin_revoke_choice_kb, admin_broadcast_confirm_kb, admin_sender_choice_kb
from services.deploy_manager import stop_bot_process
from aiogram.exceptions import TelegramBadRequest
from services.backup import backup_database

router = Router()
log = logging.getLogger("hosterbot")


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

        affordable = [g for g in gifts_result.gifts if g.star_count <= star_amount.amount]
        if not affordable:
            await callback.answer(
                f"Bot balansiga ({star_amount.amount} ⭐️) mos keladigan gift topilmadi.",
                show_alert=True,
            )
            return

        affordable.sort(key=lambda g: g.star_count)
        await callback.message.edit_text(
            f"🎁 <b>Gift tanlang</b> (bot balansi: {star_amount.amount} ⭐️):\n\n"
            f"<i>Gift sizning shu (superadmin) akkauntingizga yuboriladi va bot balansidan "
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


@router.callback_query(F.data.startswith("admin_user_view:"))
async def cb_admin_user_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = db.get_user(telegram_id)
    if user is None:
        await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return

    bots = db.list_user_bots(telegram_id)
    status_label = {"approved": "✅ Tasdiqlangan", "pending": "⏳ Kutilmoqda", "denied": "⛔️ Rad etilgan"}
    start_date = datetime.fromtimestamp(user["created_at"]).strftime("%Y-%m-%d %H:%M")

    if bots:
        bots_lines = []
        icon = {"running": "🟢 ishlayapti", "crashed": "🟡 qulagan", "stopped": "🔴 to'xtatilgan"}
        for b in bots:
            label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
            bots_lines.append(f"• {html.escape(label)} — {icon.get(b['status'], b['status'])}")
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
        f"⭐️ Balans: <b>{user['balance_stars']}</b> stars\n\n"
        f"<b>Botlari ({len(bots)}):</b>\n{bots_text}"
    )
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=admin_user_view_kb(
            telegram_id, bots, current_max_bots=user["max_bots"],
            balance=user["balance_stars"], min_withdraw=db.get_min_withdraw_stars(),
            is_banned=bool(user["is_banned"]), paid_before=user["lifetime_topup_stars"] > 0,
            viewer_is_superadmin=is_superadmin(callback.from_user.id),
            is_approved=user["status"] == "approved",
        ),
    )
    await callback.answer()


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
            is_approved=user["status"] == "approved",
        ),
    )


@router.callback_query(F.data.startswith("admin_revoke_ask:"))
async def cb_admin_revoke_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(callback.data.split(":")[1])
    user = db.get_user(target_id)
    if user is None or user["status"] != "approved":
        await callback.answer("Bu foydalanuvchida admin bergan tasdiqlangan huquq yo'q.", show_alert=True)
        return
    await callback.message.answer(
        f"⚠️ Foydalanuvchi <code>{target_id}</code>ga admin tomonidan berilgan host qilish huquqi "
        f"<b>butunlay</b> olib tashlanadi (xuddi umuman berilmagandek): tasdiqlangan holati va bot "
        f"limiti tozalanadi, hozir ishlab turgan botlari to'xtatiladi.\n\n"
        f"Bu <b>ban emas</b> — foydalanuvchi keyinchalik Stars to'lab yana bot host qila oladi. "
        f"Davom etasizmi?",
        parse_mode="HTML",
        reply_markup=admin_revoke_choice_kb(target_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_revoke_do:"))
async def cb_admin_revoke_do(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    target_id = int(callback.data.split(":")[1])
    user = db.get_user(target_id)
    if user is None or user["status"] != "approved":
        await callback.answer("Bu foydalanuvchida admin bergan tasdiqlangan huquq yo'q.", show_alert=True)
        return

    # Qanday berilgan bo'lsa (admin tasdiqi orqali), shunday ayriladi: faqat
    # status va admin bergan limit tozalanadi. is_banned'ga TEGILMAYDI — shu
    # sabab bu haqiqiy "ban" emas, foydalanuvchi Stars to'lab yana ishlata oladi.
    db.set_user_status(target_id, "pending")
    db.set_user_max_bots(target_id, None)

    stopped = []
    for b in db.list_user_bots(target_id):
        if b["status"] == "running":
            stop_bot_process(b["bot_id"])
            db.set_bot_status(b["bot_id"], "stopped", None)
            stopped.append(b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}")

    try:
        await bot.send_message(
            target_id,
            "⛔️ Sizga admin tomonidan berilgan (bepul) host qilish huquqi butunlay olib tashlandi.\n\n"
            "Yangi bot host qilish uchun endi \"💳 Hisob\" bo'limidan Stars to'lashingiz kerak bo'ladi, "
            "yoki adminga qaytadan murojaat qilishingiz mumkin.",
        )
    except Exception:
        pass

    for admin_id in set(ADMIN_IDS) | set(SUPERADMIN_IDS):
        if admin_id == callback.from_user.id:
            continue
        try:
            await bot.send_message(
                admin_id,
                f"ℹ️ {callback.from_user.first_name} foydalanuvchi <code>{target_id}</code>ning admin "
                f"bergan host huquqini butunlay olib tashladi.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    await backup_database(bot)

    stopped_text = f" To'xtatilgan botlar: {', '.join(stopped)}." if stopped else ""
    await callback.message.answer(f"✅ Host qilish huquqi butunlay olib tashlandi.{stopped_text}")

    updated_user = db.get_user(target_id)
    bots = db.list_user_bots(target_id)
    await callback.message.answer(
        "Yangilangan holat:",
        reply_markup=admin_user_view_kb(
            target_id, bots, current_max_bots=updated_user["max_bots"],
            balance=updated_user["balance_stars"], min_withdraw=db.get_min_withdraw_stars(),
            is_banned=bool(updated_user["is_banned"]), paid_before=updated_user["lifetime_topup_stars"] > 0,
            viewer_is_superadmin=is_superadmin(callback.from_user.id),
            is_approved=updated_user["status"] == "approved",
        ),
    )
    await callback.answer()


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

    db.add_user_balance(target_id, -price)
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
            is_approved=user["status"] == "approved",
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
