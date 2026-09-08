import html
import time
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin, is_superadmin
from states import ContactAdmin
from keyboards import cancel_kb, my_bots_list_kb, bot_manage_kb, admin_panel_kb
from services.deploy_manager import is_running, stop_bot_process
from services.resource_monitor import bot_ram_mb, total_bots_ram_mb, platform_ram_mb, can_start_new_bot
from services.backup import backup_database
from handlers.bot_actions import _start_single_bot

router = Router()
log = logging.getLogger("hosterbot.user_menu")


def _format_uptime(seconds: int) -> str:
    """created_at'dan buyon o'tgan vaqtni odam o'qiy oladigan shaklga o'giradi
    (masalan '3 kun 4 soat' yoki '45 daqiqa'). Faqat eng yirik ikkita birlik
    ko'rsatiladi — soniyagacha aniqlik foydalanuvchiga kerak emas."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} kun")
    if hours:
        parts.append(f"{hours} soat")
    if not days and minutes:
        parts.append(f"{minutes} daqiqa")
    return " ".join(parts) if parts else "1 daqiqadan kam"


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

    running = [b for b in bots if b["status"] == "running" and is_running(b["bot_id"])]
    crashed = [b for b in bots if b["status"] == "crashed"]
    total_ram = sum(bot_ram_mb(b["bot_id"]) for b in running)

    summary_lines = [
        f"📊 Jami: <b>{len(bots)}</b> ta bot — 🟢 {len(running)} ishlayapti",
    ]
    if crashed:
        summary_lines.append(f"🟡 {len(crashed)} ta qulagan (\"ℹ️ Bot haqida\"dan log ko'ring)")
    if running:
        summary_lines.append(f"💾 Jami RAM sarfi: {total_ram:.0f} MB")

    await message.answer("\n".join(summary_lines), parse_mode="HTML")
    await message.answer("Sizning botlaringiz:", reply_markup=my_bots_list_kb(bots))


@router.callback_query(F.data == "bot_list_back")
async def back_to_bot_list(callback: CallbackQuery):
    bots = db.list_user_bots(callback.from_user.id)
    await callback.message.edit_text("Sizning botlaringiz:", reply_markup=my_bots_list_kb(bots))
    await callback.answer()


@router.callback_query(F.data == "bots_stop_all")
async def bots_stop_all(callback: CallbackQuery, bot: Bot):
    # DIQQAT: bu bitta-bitta to'xtatish (bot_stop) funksiyasini ALMASHTIRMAYDI —
    # shunchaki tezkor qo'shimcha variant. Har bir bot uchun oddiy cb_bot_stop
    # bilan bir xil ketma-ketlik (stop_bot_process + set_bot_status).
    bots = [b for b in db.list_user_bots(callback.from_user.id) if b["status"] == "running"]
    if not bots:
        await callback.answer("Ishlab turgan bot yo'q.", show_alert=True)
        return

    await callback.answer(f"⏳ {len(bots)} ta bot to'xtatilmoqda...")
    for b in bots:
        try:
            stop_bot_process(b["bot_id"])
            db.set_bot_status(b["bot_id"], "stopped", None)
        except Exception:
            log.exception(f"bots_stop_all: bot #{b['bot_id']} to'xtatishda xato")
    await backup_database(bot)

    fresh_bots = db.list_user_bots(callback.from_user.id)
    await callback.message.edit_text(
        f"✅ {len(bots)} ta bot to'xtatildi.\n\nSizning botlaringiz:",
        reply_markup=my_bots_list_kb(fresh_bots),
    )


@router.callback_query(F.data == "bots_start_all")
async def bots_start_all(callback: CallbackQuery, bot: Bot):
    # DIQQAT: bitta-bitta ishga tushirish (bot_start) funksiyasi o'zgarishsiz
    # qoladi — bu ham shunchaki qo'shimcha tezkor variant, xuddi shu
    # _start_single_bot logikasidan (RAM byudjeti, ban tekshiruvi, kod
    # tiklash) foydalanadi, faqat ketma-ket bir nechta bot uchun.
    bots = [b for b in db.list_user_bots(callback.from_user.id) if b["status"] != "running"]
    if not bots:
        await callback.answer("Barcha botlar allaqachon ishlab turibdi.", show_alert=True)
        return

    await callback.answer(f"⏳ {len(bots)} ta bot ishga tushirilmoqda...")
    started, failed = [], []
    for b in bots:
        ok, label_or_error = await _start_single_bot(b["bot_id"], bot)
        if ok:
            started.append(label_or_error)
        else:
            failed.append(label_or_error)
    await backup_database(bot)

    lines = []
    if started:
        lines.append(f"✅ Ishga tushdi ({len(started)}): " + ", ".join(started))
    if failed:
        lines.append(f"⚠️ Ishga tushmadi ({len(failed)}):\n" + "\n".join(f"• {f}" for f in failed))

    fresh_bots = db.list_user_bots(callback.from_user.id)
    await callback.message.answer("\n\n".join(lines), reply_markup=my_bots_list_kb(fresh_bots))


@router.callback_query(F.data.startswith("bot_manage:"))
async def manage_bot(callback: CallbackQuery):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    is_live = bot_row["status"] == "running" and is_running(bot_id)
    status_line = "🟢 ishlayapti" if is_live else ("🟡 qulagan" if bot_row["status"] == "crashed" else "🔴 to‘xtatilgan")
    age_line = f"Deploy qilingan: {_format_uptime(time.time() - bot_row['created_at'])} oldin"

    text = (
        f"🤖 <b>{html.escape(bot_row['bot_username'] or bot_row['display_name'] or 'Nomsiz bot')}</b>\n"
        f"Holati: {status_line}\n"
        f"Til: {html.escape(bot_row['language'] or '-')}\n"
        f"{age_line}"
    )
    if is_live:
        text += f"\n💾 RAM: {bot_ram_mb(bot_id):.1f} MB"
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=bot_manage_kb(bot_row, has_env=bool(db.list_envs(bot_id))),
    )
    await callback.answer()


@router.message(F.text == "🛠 Admin panel")
async def admin_panel_entry(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Admin panel:", reply_markup=admin_panel_kb(is_superadmin=is_superadmin(message.from_user.id)))
