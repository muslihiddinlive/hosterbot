import html
import time

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
from config import is_admin
from services.backup import backup_database
from keyboards import main_menu_kb

router = Router()


class ReplyToUser(StatesGroup):
    waiting_text = State()


class ApproveGrantLimit(StatesGroup):
    waiting_number = State()
    waiting_hours = State()


# callback_data formatlari: req_deny:<id> | req_approve:<id> | req_reply:<id>

@router.callback_query(F.data.startswith("req_"))
async def on_request_action(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Sizga bu amal uchun ruxsat yo'q.", show_alert=True)
        return

    action, request_id_str = callback.data.split(":")
    request_id = int(request_id_str)
    request = db.get_request(request_id)
    if request is None:
        await callback.answer("So'rov topilmadi.", show_alert=True)
        return

    if action == "req_approve":
        await state.update_data(approve_request_id=request_id)
        await state.set_state(ApproveGrantLimit.waiting_number)
        await callback.message.answer(
            "Bu foydalanuvchiga nechta bot host qilishga ruxsat berasiz? Raqam yozing.\n"
            "(Standart limit endi avtomatik berilmaydi — har safar aniq belgilashingiz kerak.)"
        )
        await callback.answer()
        return
    elif action == "req_deny":
        db.set_user_status(request["telegram_id"], "denied")

    await state.update_data(reply_request_id=request_id, reply_action=action)
    await state.set_state(ReplyToUser.waiting_text)

    action_label = {
        "req_deny": "❌ Bekor qilindi. Endi",
        "req_reply": "Endi",
    }[action]
    await callback.message.answer(f"{action_label} foydalanuvchiga yubormoqchi bo'lgan javobingizni yozing:")
    await callback.answer()


@router.message(ApproveGrantLimit.waiting_number)
async def approve_grant_limit_entered(message: Message, state: FSMContext):
    data = await state.get_data()
    request_id = data.get("approve_request_id")
    request = db.get_request(request_id)
    if request is None:
        await state.clear()
        await message.answer("Xatolik: so'rov topilmadi.")
        return

    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat butun son yuboring (masalan: 3).")
        return

    limit = int(text)
    await state.update_data(approve_limit=limit)
    await state.set_state(ApproveGrantLimit.waiting_hours)
    await message.answer(
        f"Endi bu {limit} ta bot limiti necha soatga amal qilsin? Raqam yozing (masalan: 24, 72, 720).\n"
        f"(Muddat ham majburiy — o'tgach, ruxsat avtomatik qaytarib olinadi.)"
    )


@router.message(ApproveGrantLimit.waiting_hours)
async def approve_grant_hours_entered(message: Message, state: FSMContext):
    data = await state.get_data()
    request_id = data.get("approve_request_id")
    limit = data.get("approve_limit")
    request = db.get_request(request_id)
    if request is None:
        await state.clear()
        await message.answer("Xatolik: so'rov topilmadi.")
        return

    text = (message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer("Iltimos, musbat butun son yuboring (soat sifatida, masalan: 24).")
        return

    hours = int(text)
    approved_until = int(time.time()) + hours * 3600

    db.set_user_status(request["telegram_id"], "approved")
    db.set_user_max_bots(request["telegram_id"], limit)
    db.set_user_approved_until(request["telegram_id"], approved_until)

    await state.update_data(reply_request_id=request_id, reply_action="req_approve")
    await state.set_state(ReplyToUser.waiting_text)
    await message.answer(
        f"✅ Ruxsat berildi — {limit} ta bot limiti, {hours} soat muddat bilan. "
        f"Endi foydalanuvchiga yubormoqchi bo'lgan javobingizni yozing:"
    )


@router.message(ReplyToUser.waiting_text)
async def send_reply_to_user(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    request_id = data.get("reply_request_id")
    action = data.get("reply_action")
    request = db.get_request(request_id)

    has_media = bool(
        message.photo or message.video or message.animation or message.document
        or message.voice or message.video_note or message.sticker or message.audio
    )
    if not message.text and not message.caption and not has_media:
        await message.answer("Iltimos, matn, rasm, video, fayl yoki GIF yuboring.")
        return

    await state.clear()

    if request is None:
        await message.answer("Xatolik: so'rov topilmadi.")
        return

    target_id = request["telegram_id"]

    prefix = {
        "req_approve": "✅ <b>Sizga ruxsat berildi!</b>",
        "req_deny": "❌ <b>Afsuski, so'rovingiz rad etildi.</b>",
        "req_reply": "💬 <b>Admindan javob:</b>",
    }.get(action, "")

    try:
        await message.bot.send_message(target_id, prefix, parse_mode="HTML")
        if has_media:
            # DIQQAT: avval faqat matn qabul qilinardi — endi copy_to() orqali
            # istalgan turdagi xabar (rasm, video, GIF/animation, hujjat va h.k.)
            # ham asl formatida (caption bilan) foydalanuvchiga yuboriladi.
            await message.copy_to(target_id)
        elif message.text:
            await message.bot.send_message(target_id, html.escape(message.text), parse_mode="HTML")
    except Exception:
        await message.answer("Foydalanuvchiga xabar yuborib bo'lmadi (u botni bloklagan bo'lishi mumkin).")
        return

    if action == "req_approve":
        await message.bot.send_message(
            target_id,
            "Endi quyidagi tugmalardan birini tanlashingiz mumkin:",
            reply_markup=main_menu_kb(is_admin=is_admin(target_id)),
        )

    if action in ("req_approve", "req_deny"):
        await backup_database(bot)

    await message.answer("Xabar foydalanuvchiga yuborildi. ✅")
