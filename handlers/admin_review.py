import html

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
        db.set_user_status(request["telegram_id"], "approved")
    elif action == "req_deny":
        db.set_user_status(request["telegram_id"], "denied")
    # req_reply -> statusni o'zgartirmaymiz, faqat javob yozamiz

    await state.update_data(reply_request_id=request_id, reply_action=action)
    await state.set_state(ReplyToUser.waiting_text)

    action_label = {
        "req_approve": "✅ Ruxsat berildi. Endi",
        "req_deny": "❌ Bekor qilindi. Endi",
        "req_reply": "Endi",
    }[action]
    await callback.message.answer(f"{action_label} foydalanuvchiga yubormoqchi bo'lgan javobingizni yozing:")
    await callback.answer()


@router.message(ReplyToUser.waiting_text)
async def send_reply_to_user(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    request_id = data.get("reply_request_id")
    action = data.get("reply_action")
    request = db.get_request(request_id)
    await state.clear()

    if request is None:
        await message.answer("Xatolik: so'rov topilmadi.")
        return

    target_id = request["telegram_id"]

    prefix = {
        "req_approve": "✅ <b>Sizga ruxsat berildi!</b>\n\n",
        "req_deny": "❌ <b>Afsuski, so'rovingiz rad etildi.</b>\n\n",
        "req_reply": "💬 <b>Admindan javob:</b>\n\n",
    }.get(action, "")

    try:
        await message.bot.send_message(target_id, prefix + html.escape(message.text), parse_mode="HTML")
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
