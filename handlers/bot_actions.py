import html
import logging
import os

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, FSInputFile
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin
from states import ConfirmDelete
from keyboards import bot_manage_kb, admin_bot_view_kb, cancel_kb, main_menu_kb
from services.deploy_manager import start_bot_process, stop_bot_process, read_log_tail
from services.file_utils import cleanup_bot_files, write_env_file
from services.backup import backup_database

router = Router()
log = logging.getLogger("hosterbot.bot_actions")


def _authorized(callback: CallbackQuery, bot_row) -> bool:
    return bot_row is not None and (bot_row["owner_id"] == callback.from_user.id or is_admin(callback.from_user.id))


def _refresh_kb(bot_row, callback: CallbackQuery):
    if is_admin(callback.from_user.id) and bot_row["owner_id"] != callback.from_user.id:
        return admin_bot_view_kb(bot_row)
    return bot_manage_kb(bot_row)


@router.callback_query(F.data.startswith("bot_start:"))
async def cb_bot_start(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    envs = {row["key"]: row["value"] for row in db.list_envs(bot_id)}
    pid = start_bot_process(bot_id, bot_row["code_path"], bot_row["start_cmd"], envs)
    db.set_bot_status(bot_id, "running", pid)
    bot_row = db.get_bot(bot_id)

    await callback.message.edit_text(
        f"🤖 <b>{html.escape(bot_row['bot_username'] or bot_row['display_name'] or 'Nomsiz bot')}</b>\nHolati: 🟢 ishlayapti",
        parse_mode="HTML", reply_markup=_refresh_kb(bot_row, callback),
    )
    await backup_database(bot)
    await callback.answer("Bot ishga tushirildi ✅")


@router.callback_query(F.data.startswith("bot_stop:"))
async def cb_bot_stop(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    stop_bot_process(bot_id)
    db.set_bot_status(bot_id, "stopped", None)
    bot_row = db.get_bot(bot_id)

    await callback.message.edit_text(
        f"🤖 <b>{html.escape(bot_row['bot_username'] or bot_row['display_name'] or 'Nomsiz bot')}</b>\nHolati: 🔴 to‘xtatilgan",
        parse_mode="HTML", reply_markup=_refresh_kb(bot_row, callback),
    )
    await backup_database(bot)
    await callback.answer("Bot to'xtatildi ⏹")


@router.callback_query(F.data.startswith("bot_delete:"))
async def cb_bot_delete(callback: CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
    confirm_phrase = f"Men {label}imni o'chirib yuborishni istayman"

    await state.update_data(delete_bot_id=bot_id, delete_confirm_phrase=confirm_phrase)
    await state.set_state(ConfirmDelete.waiting_text)

    await callback.message.answer(
        f"⚠️ Botni o'chirish qaytarib bo'lmaydi (kod, log va env'lar butunlay o'chadi).\n\n"
        f"Tasdiqlash uchun <b>aynan shu matnni</b> yozing:\n\n"
        f"<code>{html.escape(confirm_phrase)}</code>\n\n"
        f"Bekor qilish uchun pastdagi tugmani bosing.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(ConfirmDelete.waiting_text)
async def confirm_delete_text(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    bot_id = data.get("delete_bot_id")
    confirm_phrase = data.get("delete_confirm_phrase", "")

    if message.text.strip() != confirm_phrase:
        await message.answer(
            f"❌ Matn mos kelmadi. Tasdiqlash uchun <b>aynan</b> shu matnni yozing:\n\n"
            f"<code>{html.escape(confirm_phrase)}</code>\n\n"
            f"Yoki bekor qiling.",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
        return

    await state.clear()

    bot_row = db.get_bot(bot_id)
    if bot_row is None or not (bot_row["owner_id"] == message.from_user.id or is_admin(message.from_user.id)):
        await message.answer("Ruxsat yo'q yoki bot topilmadi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))
        return

    stop_bot_process(bot_id)
    db.delete_bot(bot_id)
    cleanup_bot_files(bot_id)
    await backup_database(bot)

    await message.answer("🗑 Bot muvaffaqiyatli o'chirildi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))


@router.callback_query(F.data.startswith("bot_info:"))
async def cb_bot_info(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    try:
        envs = db.list_envs(bot_id)
        env_text = "\n".join(
            f"• <code>{html.escape(r['key'])}</code> = <code>{html.escape(r['value'])}</code>" for r in envs
        ) or "ENV yo'q"
        logs = read_log_tail(bot_row["code_path"])

        info_text = (
            f"ℹ️ <b>{html.escape(bot_row['bot_username'] or bot_row['display_name'] or 'Nomsiz bot')}</b>\n\n"
            f"Til: {html.escape(bot_row['language'] or '-')}\n"
            f"Build: <code>{html.escape(bot_row['build_cmd'] or '-')}</code>\n"
            f"Start: <code>{html.escape(bot_row['start_cmd'] or '-')}</code>\n\n"
            f"<b>ENV:</b>\n{env_text}\n\n"
            f"<b>So'nggi loglar:</b>\n<pre>{html.escape(logs[-2500:])}</pre>"
        )
        await callback.message.answer(info_text, parse_mode="HTML")
    except Exception as e:
        log.warning(f"bot_info yuborishda xatolik (bot_id={bot_id}): {e}")
        await callback.message.answer(
            "⚠️ Ma'lumotni to'liq ko'rsatishda xatolik yuz berdi (loglar juda uzun yoki maxsus belgilar bor). "
            "Loglarni pastda yalang'och matn sifatida yuboraman:"
        )
        try:
            await callback.message.answer(read_log_tail(bot_row["code_path"])[-3500:])
        except Exception:
            pass

    # Original kod faylini ham yuboramiz (backup guruhidan file_id orqali)
    if bot_row["storage_file_id"]:
        try:
            await bot.send_document(callback.from_user.id, bot_row["storage_file_id"], caption="📄 Original kod fayli")
        except Exception:
            pass

    await callback.answer()
