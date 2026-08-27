import html
from datetime import datetime

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin, STORAGE_GROUP_ID
from states import AdminMessageUser
from keyboards import admin_all_bots_kb, admin_bot_view_kb, owner_info_kb, admin_users_kb, admin_user_view_kb, admin_panel_kb
from services.backup import backup_database

router = Router()


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
    await callback.message.edit_text("Admin panel:", reply_markup=admin_panel_kb())
    await callback.answer()


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
        f"Holati: {status_label.get(user['status'], user['status'])}\n"
        f"/start bosgan sana: {start_date}\n"
        f"⭐️ Balans: <b>{user['balance_stars']}</b> stars\n\n"
        f"<b>Botlari ({len(bots)}):</b>\n{bots_text}"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_user_view_kb(telegram_id, bots))
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
async def send_admin_msg(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    target_id = data.get("msg_target_id")
    await state.clear()
    try:
        await bot.send_message(target_id, f"💬 <b>Admindan xabar:</b>\n\n{html.escape(message.text)}", parse_mode="HTML")
        await message.answer("Xabar yuborildi ✅")
    except Exception:
        await message.answer("Xabar yuborilmadi (foydalanuvchi botni bloklagan bo'lishi mumkin).")


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
