"""
handlers/stars.py

Admin tasdiqisiz, Telegram Stars orqali o'z-o'zini hostlash (self-service).
Narx: STARS_PER_UNIT (default 3) ta stars = SECONDS_PER_UNIT (default 24 soat)
hosting huquqi, aniq soniyagacha.

MUHIM DIZAYN QARORI: haqiqiy "soniyama-soniya" balansdan yechish (continuous
metering) o'rniga, har bir to'lov/uzaytirish paytida botning "paid_until"
(epoch) vaqtiga aniq shu davr qo'shiladi. Bu: (1) DB'ga har soniyada yozish
kerak bo'lmaydi (Render Free Tier'da keraksiz I/O), (2) foydalanuvchiga
"qolgan vaqt" ni soniyagacha aniq ko'rsatish uchun yetarli (paid_until - now),
(3) muddat tugaganda billing_watchdog (main.py) avtomatik to'xtatadi.
"""
import time
import html
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin
from states import StarsTopUp
from keyboards import hisob_kb, cancel_kb, main_menu_kb
from services.deploy_manager import start_bot_process, is_running
from services.resource_monitor import can_start_new_bot, format_ram_limit_message

router = Router()


def format_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "muddati tugagan"
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}kun")
    if h or d:
        parts.append(f"{h}soat")
    if m or h or d:
        parts.append(f"{m}min")
    parts.append(f"{s}sek")
    return " ".join(parts)


@router.message(F.text == "💳 Hisob")
async def show_balance(message: Message):
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    balance = db.get_user_balance(message.from_user.id)
    stars_per_unit = db.get_stars_per_unit()
    now = int(time.time())

    hosted_bots = [
        b for b in db.list_user_bots(message.from_user.id)
        if b["stars_hosted"] and b["status"] == "running"
    ]

    lines = [
        f"⭐️ <b>Balansingiz: {balance} stars</b>\n",
        f"Narx: <b>{db.get_stars_per_unit()} stars = 24 soat</b> hosting (1 bot uchun), soniyagacha aniq hisoblanadi.",
    ]
    if hosted_bots:
        lines.append("\n<b>Stars orqali hostlangan botlaringiz:</b>")
        for b in hosted_bots:
            label = b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}"
            remaining = (b["paid_until"] or now) - now
            lines.append(f"• {html.escape(label)} — qolgan vaqt: {format_remaining(remaining)}")
    else:
        lines.append(
            "\nHozircha Stars orqali hostlangan botingiz yo'q. Balansni to'ldirib, "
            "\"➕ Bot qo'shish\" orqali (admin tasdig'isiz) bot deploy qila olasiz."
        )

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=hisob_kb(hosted_bots))


@router.callback_query(F.data == "stars_topup")
async def cb_stars_topup(callback: CallbackQuery, state: FSMContext):
    await state.set_state(StarsTopUp.waiting_amount)
    await callback.message.answer(
        f"Necha ⭐️ Stars to'lamoqchisiz? Raqam yozing (masalan: {db.get_stars_per_unit()} — bu 24 soatlik hosting narxi).\n\n"
        f"Ko'proq to'lasangiz, balansingizga qo'shilib qoladi — botlaringizni istalgan vaqt uzaytirish uchun ishlatasiz.",
    )
    await callback.answer()


@router.message(StarsTopUp.waiting_amount)
async def stars_amount_entered(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat butun son yuboring (masalan: 3).")
        return

    amount = int(text)
    await state.clear()

    await message.answer_invoice(
        title=f"{amount} ⭐️ Stars — HosterBot balans",
        description=f"Balansingizga {amount} Stars qo'shiladi. {db.get_stars_per_unit()} stars = 24 soat bot hosting.",
        payload=f"topup_{message.from_user.id}_{amount}",
        provider_token="",  # Telegram Stars uchun bo'sh qoldiriladi
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} Stars", amount=amount)],
    )


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment = message.successful_payment
    amount = payment.total_amount  # XTR uchun bu to'g'ridan-to'g'ri stars soni
    user_id = message.from_user.id

    db.add_lifetime_topup(user_id, amount)  # umr bo'yi to'lov hisoblagichi — hech qachon kamaymaydi
    user = db.get_user(user_id)

    # Agar user hozir bloklangan bo'lsa va yetarli miqdorda to'lasa — darhol ochamiz,
    # lekin xizmat haqi sifatida bir qismini ushlab qolamiz (qolgani balansga tushadi).
    min_stars = db.get_unblock_min_stars()
    fee_pct = db.get_unblock_fee_percent()
    if user and user["is_banned"] and amount >= min_stars:
        fee = (amount * fee_pct) // 100
        credited = amount - fee
        db.add_user_balance(user_id, credited, reason=f"To'lov ({amount}⭐️, {fee}⭐️ xizmat haqi ushlab qolindi, blok ochildi)")
        db.set_user_banned(user_id, False)
        db.set_user_blocked_until(user_id, None)
        new_balance = db.get_user_balance(user_id)
        await message.answer(
            f"✅ To'lov qabul qilindi — <b>{amount} ⭐️</b>.\n"
            f"Xizmat haqi: {fee} ⭐️ ({fee_pct}%), balansga qo'shildi: {credited} ⭐️.\n"
            f"Yangi balans: <b>{new_balance} ⭐️</b>\n\n"
            f"🔓 Host qilish huquqingiz <b>darhol tiklandi</b>.",
            parse_mode="HTML",
            reply_markup=main_menu_kb(is_admin=is_admin(user_id)),
        )
        return

    db.add_user_balance(user_id, amount, reason="Balans to'ldirish")
    new_balance = db.get_user_balance(user_id)

    if user and user["is_banned"]:
        # Bloklangan, lekin yetarli emas — balans qo'shildi, lekin hali bloklangan holda qoladi.
        await message.answer(
            f"To'lov qabul qilindi, balansga <b>{amount} ⭐️</b> qo'shildi (yangi balans: {new_balance} ⭐️).\n\n"
            f"⏸ Lekin host qilish huquqingiz hali bloklangan. Darhol ochish uchun bir martada "
            f"kamida <b>{min_stars} ⭐️</b> to'lashingiz kerak, aks holda avvalgi shartlaringiz bo'yicha kuting.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"✅ To'lov qabul qilindi! Balansga <b>{amount} ⭐️</b> qo'shildi.\n"
        f"Yangi balans: <b>{new_balance} ⭐️</b>\n\n"
        f"Endi \"➕ Bot qo'shish\" orqali (admin tasdig'isiz) bot host qila olasiz, "
        f"yoki \"💳 Hisob\"dan mavjud botingizni uzaytiring.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)),
    )


@router.callback_query(F.data.startswith("stars_extend:"))
async def cb_stars_extend(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    if db.is_banned(bot_row["owner_id"]):
        await callback.answer("⛔️ Ruxsatingiz olib tashlangan — uzaytirib bo'lmaydi.", show_alert=True)
        return

    stars_per_unit = db.get_stars_per_unit()
    seconds_per_unit = db.get_seconds_per_unit()
    balance = db.get_user_balance(bot_row["owner_id"])
    if balance < stars_per_unit:
        await callback.answer(
            f"⭐️ Balansingiz yetarli emas ({balance}/{stars_per_unit}). Avval to'ldiring.",
            show_alert=True,
        )
        return

    db.add_user_balance(bot_row["owner_id"], -stars_per_unit, reason=f"Bot uzaytirish (#{bot_id}, +24 soat)")
    now = int(time.time())
    base = bot_row["paid_until"] if (bot_row["paid_until"] and bot_row["paid_until"] > now) else now
    new_paid_until = base + seconds_per_unit
    db.set_bot_stars_payment(bot_id, new_paid_until)

    # Agar bot muddati tugab to'xtagan bo'lsa, RAM byudjetini tekshirib qayta ishga tushiramiz
    if bot_row["status"] != "running" or not is_running(bot_id):
        allowed, used_mb, budget_mb = can_start_new_bot()
        if not allowed:
            ram_note = format_ram_limit_message(callback.from_user.id, used_mb, budget_mb)
            await callback.answer(
                f"⭐️ To'lov qabul qilindi va vaqt uzaytirildi, lekin {ram_note.lstrip('⚠️ ')}",
                show_alert=True,
            )
            return
        envs = {row["key"]: row["value"] for row in db.list_envs(bot_id)}
        pid = start_bot_process(bot_id, bot_row["code_path"], bot_row["start_cmd"], envs)
        db.set_bot_status(bot_id, "running", pid)

    remaining = new_paid_until - now
    await callback.answer(f"✅ Yana {stars_per_unit}⭐️ yechildi. Qolgan vaqt: {format_remaining(remaining)}", show_alert=True)


@router.callback_query(F.data == "stars_history")
async def cb_stars_history(callback: CallbackQuery):
    entries = db.get_user_ledger(callback.from_user.id, limit=20)
    if not entries:
        await callback.answer("Hozircha to'lov tarixi bo'sh.", show_alert=True)
        return

    lines = ["📜 <b>To'lov tarixi</b> (so'nggi 20 ta):\n"]
    for e in entries:
        dt = datetime.fromtimestamp(e["created_at"]).strftime("%Y-%m-%d %H:%M")
        sign = "+" if e["delta"] >= 0 else ""
        lines.append(f"{sign}{e['delta']} ⭐️ — {html.escape(e['reason'])} ({dt})")

    await callback.message.answer("\n".join(lines), parse_mode="HTML")
    await callback.answer()
