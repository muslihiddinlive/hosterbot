import html

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin
from states import ContactAdmin
from keyboards import cancel_kb, my_bots_list_kb, bot_manage_kb, admin_panel_kb

router = Router()


@router.message(F.text == "📩 Adminga habar berish")
async def contact_admin_again(message: Message, state: FSMContext):
    await message.answer("Adminga yubormoqchi bo'lgan xabaringizni yozing:", reply_markup=cancel_kb())
    await state.set_state(ContactAdmin.waiting_message)


@router.message(F.text == "🤖 Mening botlarim")
async def my_bots(message: Message):
    bots = db.list_user_bots(message.from_user.id)
    if not bots:
        await message.answer("Siz hali birorta ham bot deploy qilmagansiz. \"➕ Bot qo'shish\" tugmasidan foydalaning.")
        return
    await message.answer("Sizning botlaringiz:", reply_markup=my_bots_list_kb(bots))


@router.callback_query(F.data == "bot_list_back")
async def back_to_bot_list(callback: CallbackQuery):
    bots = db.list_user_bots(callback.from_user.id)
    await callback.message.edit_text("Sizning botlaringiz:", reply_markup=my_bots_list_kb(bots))
    await callback.answer()


@router.callback_query(F.data.startswith("bot_manage:"))
async def manage_bot(callback: CallbackQuery):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return
    text = (
        f"🤖 <b>{html.escape(bot_row['bot_username'] or bot_row['display_name'] or 'Nomsiz bot')}</b>\n"
        f"Holati: {'🟢 ishlayapti' if bot_row['status'] == 'running' else '🔴 to‘xtatilgan'}\n"
        f"Til: {html.escape(bot_row['language'] or '-')}\n"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=bot_manage_kb(bot_row))
    await callback.answer()


@router.message(F.text == "🛠 Admin panel")
async def admin_panel_entry(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Admin panel:", reply_markup=admin_panel_kb())
