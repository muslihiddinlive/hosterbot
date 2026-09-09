import html

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database as db
from config import ADMIN_IDS, SUPERADMIN_IDS, is_admin
from states import ContactAdmin
from keyboards import main_menu_kb, cancel_kb, admin_review_kb, self_service_menu_kb

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

    if is_admin(message.from_user.id):
        user = db.get_user(message.from_user.id)
        if user["status"] != "approved":
            db.set_user_status(message.from_user.id, "approved")
        await message.answer(
            "Assalomu alaykum, admin! 👋\n\nQuyidagi tugmalardan birini tanlang:",
            reply_markup=main_menu_kb(is_admin=True),
        )
        return

    user = db.get_user(message.from_user.id)

    if user["status"] == "approved":
        await message.answer(
            "Assalomu alaykum, qaytganingizdan xursandmiz! 👋\n\n"
            "Sizga botlar deploy qilish uchun ruxsat berilgan. Quyidagi tugmalardan birini tanlang:",
            reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)),
        )
        return

    if user["status"] == "pending":
        await message.answer(
            "Salom! 👋 Siz allaqachon so'rov yubordingiz, admin javobini kuting.\n\n"
            "Kutishni istamasangiz, \"💳 Hisob\" orqali Stars bilan to'lab, admin tasdig'isiz "
            "o'zingiz ham bot host qila olasiz.",
            reply_markup=self_service_menu_kb(),
        )
        return

    # yangi yoki denied bo'lgan user
    await message.answer(
        "Assalomu alaykum! 👋\n\n"
        "Bu bot orqali siz o'z Telegram botlaringizni deploy qila olasiz. Ikkita yo'l bor:\n\n"
        "1️⃣ Adminga xabar yuboring — u tasdiqlagach, bepul cheklovsiz foydalanasiz\n"
        f"2️⃣ \"💳 Hisob\"dan Stars bilan to'lab, admin tasdig'isiz darhol o'zingiz host qiling "
        f"({db.get_stars_per_unit()} ⭐️ = 24 soat)\n\n"
        "Quyidagi tugmalardan birini tanlang:",
        reply_markup=self_service_menu_kb(),
    )


@router.message(F.text == "⛔️ Bekor qilish")
async def cancel_any(message: Message, state: FSMContext):
    await state.clear()
    user = db.get_user(message.from_user.id)
    if user and user["status"] == "approved":
        await message.answer("Bekor qilindi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))
    else:
        await message.answer("Bekor qilindi.", reply_markup=self_service_menu_kb())


@router.message(ContactAdmin.waiting_message)
async def forward_to_admin(message: Message, state: FSMContext):
    # DIQQAT: avval faqat matn qabul qilinardi (rasm/fayl/gif yuborilsa rad
    # etilardi). Endi copy_to() orqali istalgan turdagi xabar (rasm, video,
    # GIF/animation, hujjat, ovozli xabar, sticker va h.k.) formatini saqlab
    # adminga yuboriladi — "Forwarded from" belgisisiz, chunki foydalanuvchi
    # kimligini o'zimiz alohida xabar bilan ko'rsatamiz.
    if not message.text and not message.caption and not (
        message.photo or message.video or message.animation or message.document
        or message.voice or message.video_note or message.sticker or message.audio
    ):
        await message.answer("Iltimos, matn, rasm, video, fayl yoki GIF yuboring.")
        return

    await state.clear()
    preview_text = message.text or message.caption or "(media, matnsiz)"
    request_id = db.create_pending_request(message.from_user.id, preview_text)

    user = message.from_user
    admin_text = (
        f"📩 <b>Yangi murojaat</b>\n\n"
        f"👤 Foydalanuvchi: {html.escape(user.full_name)}"
        f"{' (@' + user.username + ')' if user.username else ''}\n"
        f"🆔 ID: <code>{user.id}</code>"
    )

    targets = ADMIN_IDS | SUPERADMIN_IDS
    if not targets:
        await message.answer(
            "Xabaringiz qabul qilindi, lekin hozircha adminlar sozlanmagan. Keyinroq urinib ko'ring."
        )
        return

    sent_any = False
    for admin_id in targets:
        try:
            # Avval kim yuborganini ko'rsatuvchi xabar, keyin uning o'zi
            # yuborgan xabarning aynan o'zi (media bilan yoki matn bilan).
            sent = await message.bot.send_message(admin_id, admin_text, parse_mode="HTML")
            await message.copy_to(admin_id)
            await message.bot.send_message(admin_id, "⬆️ Yuqoridagi murojaatga javob berish:", reply_markup=admin_review_kb(request_id))
            db.set_request_admin_msg(request_id, sent.message_id)
            sent_any = True
        except Exception:
            continue

    if sent_any:
        await message.answer("✅ Xabaringiz adminga yuborildi. Javobini kuting.")
    else:
        await message.answer("Xabaringizni yuborishda xatolik yuz berdi, keyinroq urinib ko'ring.")
