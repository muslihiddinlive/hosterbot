import html
import logging
import os
import re
import asyncio

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, FSInputFile
from aiogram.fsm.context import FSMContext

import database as db
from config import is_admin, is_superadmin, STORAGE_GROUP_ID, WEBHOOK_BASE_URL
from states import ConfirmDelete, FixCode, FixRequirements, FixEnv, RenameBot
from keyboards import bot_manage_kb, admin_bot_view_kb, cancel_kb, main_menu_kb, edit_bot_menu_kb
from services.deploy_manager import start_bot_process, stop_bot_process, read_log_tail, is_running, format_log_block, run_build_command, bot_link_html
from services.resource_monitor import can_start_new_bot, bot_ram_mb, format_ram_limit_message
from services.file_utils import (
    cleanup_bot_files, write_env_file, extract_zip, resolve_project_root,
    normalize_requirements_filename, fix_all_py_encodings, fix_py_encoding,
    find_requirements_txt,
)
from services.backup import backup_database
from services.ai_client import ask_ai, build_crash_diagnosis_prompt, AIError

router = Router()
log = logging.getLogger("hosterbot.bot_actions")


def _authorized(callback: CallbackQuery, bot_row) -> bool:
    return bot_row is not None and (bot_row["owner_id"] == callback.from_user.id or is_admin(callback.from_user.id))


def _refresh_kb(bot_row, callback: CallbackQuery):
    if is_admin(callback.from_user.id) and bot_row["owner_id"] != callback.from_user.id:
        return admin_bot_view_kb(bot_row)
    return bot_manage_kb(
        bot_row, has_env=bool(db.list_envs(bot_row["bot_id"])),
        viewer_is_vip=is_admin(callback.from_user.id),
    )


async def _ensure_code_present(bot: Bot, bot_row) -> tuple[bool, str]:
    """
    Render qayta ko'tarilganda faqat 'running' holatdagi botlar avtomatik
    tiklanadi (main.py restore_running_bots) — 'crashed'/'stopped' holatda
    qolgan botlar ephemeral diskdan yo'qolgan bo'lishi mumkin. Shu sabab qo'lda
    "Qayta ishga tushirish" bosilganda ham, kod bor-yo'qligini tekshirib,
    kerak bo'lsa Telegram storage guruhidan qayta tiklaymiz — aks holda
    start_bot_process run.log ochishda "No such file or directory" xatosi bilan
    qulab tushar edi.

    Qaytaradi: (muvaffaqiyatmi, xato_matni_yoki_bosh)
    """
    workdir = bot_row["code_path"]
    code_present = os.path.isdir(workdir) and any(
        f.endswith(".py") for _, _, files in os.walk(workdir) for f in files
    )
    if code_present:
        return True, ""

    if not bot_row["storage_file_id"]:
        return False, "Bot kodi diskdan yo'qolgan va tiklash uchun zaxira (storage_file_id) topilmadi."

    try:
        os.makedirs(workdir, exist_ok=True)
        tmp_path = os.path.join(workdir, "_restore_download")
        await bot.download(bot_row["storage_file_id"], destination=tmp_path)
        if bot_row["is_zip"]:
            extract_zip(tmp_path, workdir)
            os.remove(tmp_path)
            new_workdir = resolve_project_root(workdir)
            if new_workdir != workdir:
                db.set_bot_code_path(bot_row["bot_id"], new_workdir)
                workdir = new_workdir
        else:
            match = re.search(r'([^\s"\']+\.py)', bot_row["start_cmd"] or "")
            py_name = os.path.basename(match.group(1)) if match else "main.py"
            os.replace(tmp_path, os.path.join(workdir, py_name))
        normalize_requirements_filename(workdir)
        fix_all_py_encodings(workdir)
        return True, ""
    except Exception as e:
        log.exception("Kodni tiklashda xato")
        return False, f"Kodni tiklashda xato: {e}"


async def _start_single_bot(bot_id: int, bot: Bot) -> tuple[bool, str]:
    """Bitta botni ishga tushirishning umumiy logikasi — cb_bot_start bilan bir xil
    ketma-ketlik, lekin CallbackQuery'ga bog'lanmagan holda (bulk-start uchun ham
    ishlatiladi, u yerda bitta callback ko'p botga tegishli bo'ladi).
    Qaytaradi: (muvaffaqiyatli_ishga_tushdimi, bot_label yoki xato matni)."""
    bot_row = db.get_bot(bot_id)
    if bot_row is None:
        return False, f"Bot #{bot_id} topilmadi"
    bot_label = f"@{bot_row['bot_username']}" if bot_row["bot_username"] else (bot_row["display_name"] or f"Bot #{bot_id}")

    if db.is_banned(bot_row["owner_id"]):
        return False, f"{bot_label} — egasining ruxsati olib tashlangan"

    allowed, used_mb, budget_mb = can_start_new_bot()
    if not allowed:
        return False, f"{bot_label} — {format_ram_limit_message(bot_row['owner_id'], used_mb, budget_mb)}"

    ok, err = await _ensure_code_present(bot, bot_row)
    if not ok:
        return False, f"{bot_label} — {err}"
    bot_row = db.get_bot(bot_id)

    envs = {row["key"]: row["value"] for row in db.list_envs(bot_id)}
    try:
        pid = start_bot_process(bot_id, bot_row["code_path"], bot_row["start_cmd"], envs)
    except Exception as e:
        log.exception("start_bot_process xatoligi (_start_single_bot)")
        return False, f"{bot_label} — {e}"
    db.set_bot_status(bot_id, "running", pid)
    return True, bot_label


@router.callback_query(F.data.startswith("bot_start:"))
async def cb_bot_start(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    if db.is_banned(bot_row["owner_id"]) and not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Egasining ruxsati olib tashlangan — bot ishga tushirilmaydi.", show_alert=True)
        return

    allowed, used_mb, budget_mb = can_start_new_bot()
    if not allowed:
        await callback.answer(
            format_ram_limit_message(callback.from_user.id, used_mb, budget_mb, "Avval boshqa botni to'xtating."),
            show_alert=True,
        )
        return

    await callback.answer("Tekshirilmoqda...")
    bot_label = f"@{bot_row['bot_username']}" if bot_row["bot_username"] else (bot_row["display_name"] or f"Bot #{bot_id}")
    ok, err = await _ensure_code_present(bot, bot_row)
    if not ok:
        await callback.message.answer(format_log_block(f"⚠️ {bot_label} — ishga tushirib bo'lmadi", err), parse_mode="HTML")
        return
    bot_row = db.get_bot(bot_id)  # code_path o'zgargan bo'lishi mumkin

    envs = {row["key"]: row["value"] for row in db.list_envs(bot_id)}
    try:
        pid = start_bot_process(bot_id, bot_row["code_path"], bot_row["start_cmd"], envs)
    except Exception as e:
        log.exception("start_bot_process xatoligi")
        await callback.message.answer(format_log_block(f"⚠️ {bot_label} — ishga tushirib bo'lmadi", str(e)), parse_mode="HTML")
        return
    db.set_bot_status(bot_id, "running", pid)
    bot_row = db.get_bot(bot_id)

    await callback.message.edit_text(
        f"🤖 <b>{bot_link_html(bot_row)}</b>\nHolati: 🟢 ishlayapti",
        parse_mode="HTML", reply_markup=_refresh_kb(bot_row, callback),
    )
    await backup_database(bot)


@router.callback_query(F.data.startswith("bot_rebuild:"))
async def cb_bot_rebuild(callback: CallbackQuery, bot: Bot):
    """
    "▶️ Ishga tushirish" faqat mavjud (avval build qilingan) muhitda process'ni
    qayta ishga tushiradi — agar crash sababi masalan requirements.txt'dagi yangi
    kutubxona yoki build muhitidagi muammo bo'lsa, oddiy qayta ishga tushirish
    yordam bermaydi (foydalanuvchi buni tushunmay, "ishlamayapti" deb qayta-qayta
    urinib, adminga murojaat qilardi).

    Bu tugma build bosqichini ham QAYTA bajaradi (asl kodni qayta yuklamasdan,
    diskda/tiklangan holatdagi kod ustida) — shu sabab faqat "crashed" holatdagi
    botlar uchun ko'rsatiladi (bot_manage_kb/admin_bot_view_kb).
    """
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    if db.is_banned(bot_row["owner_id"]) and not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Egasining ruxsati olib tashlangan — bot ishga tushirilmaydi.", show_alert=True)
        return

    allowed, used_mb, budget_mb = can_start_new_bot()
    if not allowed:
        await callback.answer(
            format_ram_limit_message(callback.from_user.id, used_mb, budget_mb, "Avval boshqa botni to'xtating."),
            show_alert=True,
        )
        return

    bot_label = f"@{bot_row['bot_username']}" if bot_row["bot_username"] else (bot_row["display_name"] or f"Bot #{bot_id}")
    await callback.answer("Qayta build qilinmoqda...")
    await callback.message.answer(f"⏳ {html.escape(bot_label)} — qayta build va ishga tushirilmoqda...")

    ok, err = await _ensure_code_present(bot, bot_row)
    if not ok:
        await callback.message.answer(format_log_block(f"⚠️ {bot_label} — qayta build qilib bo'lmadi", err), parse_mode="HTML")
        return
    bot_row = db.get_bot(bot_id)  # code_path o'zgargan bo'lishi mumkin

    # DIQQAT: run_build_command sinxron (blocking) subprocess.run chaqiradi — bosh
    # event loop'ni bloklab qo'ymaslik uchun (main.py'dagi avvalgi "KRITIK FIX" bilan
    # bir xil sabab) alohida thread'da ishga tushiramiz.
    log_path = os.path.join(bot_row["code_path"], "run.log")
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            build_ok = await asyncio.to_thread(run_build_command, bot_row["code_path"], bot_row["build_cmd"] or "", log_file)
    except Exception as e:
        log.exception("Qayta build qilishda xato")
        await callback.message.answer(format_log_block(f"⚠️ {bot_label} — build xatosi", str(e)), parse_mode="HTML")
        return

    if not build_ok:
        db.set_bot_status(bot_id, "crashed", None)
        error_tail = read_log_tail(bot_row["code_path"], n_lines=40)
        await callback.message.answer(
            format_log_block(f"❌ {bot_label} — build bosqichida yana xatolik", error_tail),
            parse_mode="HTML",
        )
        return

    envs = {row["key"]: row["value"] for row in db.list_envs(bot_id)}
    try:
        pid = start_bot_process(bot_id, bot_row["code_path"], bot_row["start_cmd"], envs)
    except Exception as e:
        log.exception("start_bot_process xatoligi")
        await callback.message.answer(format_log_block(f"⚠️ {bot_label} — ishga tushirib bo'lmadi", str(e)), parse_mode="HTML")
        return
    db.set_bot_status(bot_id, "running", pid)

    # Boshqa crash'lar kabi: process darhol o'lib qolganini tekshiramiz.
    await asyncio.sleep(3)
    if not is_running(bot_id):
        db.set_bot_status(bot_id, "crashed", None)
        crash_log = read_log_tail(bot_row["code_path"], n_lines=40)
        await backup_database(bot)
        await callback.message.answer(
            format_log_block(f"❌ {bot_label} — qayta ishga tushgach darhol qulab tushdi", crash_log),
            parse_mode="HTML",
        )
        return

    bot_row = db.get_bot(bot_id)
    await callback.message.answer(
        f"✅ <b>{html.escape(bot_label)}</b> qayta build qilindi va ishlab turibdi!",
        parse_mode="HTML", reply_markup=_refresh_kb(bot_row, callback),
    )
    await backup_database(bot)


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
        f"🤖 <b>{bot_link_html(bot_row)}</b>\nHolati: 🔴 to‘xtatilgan",
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
            f"ℹ️ <b>{bot_link_html(bot_row)}</b>\n\n"
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
            await bot.send_document(callback.from_user.id, bot_row["storage_file_id"], caption="📄 Kod fayli (eng so'nggi)")
        except Exception:
            pass

    # MUHIM FIX: ilgari faqat kod fayli yuborilardi — requirements.txt va ENV
    # esa umuman fayl sifatida olinmasdi (ENV faqat matn ko'rinishida ko'rsatilardi).
    # Endi "bot ishlab turgan vaqtida ham kod, ENV, requirements'ni olish" so'roviga
    # ko'ra uchalasi ham fayl sifatida yuboriladi — botning status'idan (running/
    # stopped/crashed) qat'i nazar ishlaydi, chunki bular DB/backup'dan olinadi.
    req_sent = False
    if bot_row["requirements_file_id"]:
        try:
            await bot.send_document(callback.from_user.id, bot_row["requirements_file_id"], caption="📋 requirements.txt (eng so'nggi)")
            req_sent = True
        except Exception:
            pass
    if not req_sent:
        # Alohida backup qilinmagan bo'lsa ham, disk hali joyida bo'lsa (bot
        # hozir ishlab turganida ko'pincha shunday) — workdir'dan to'g'ridan-to'g'ri o'qib yuboramiz.
        try:
            req_path = find_requirements_txt(bot_row["code_path"])
            if req_path and os.path.isfile(req_path):
                await bot.send_document(callback.from_user.id, FSInputFile(req_path, filename="requirements.txt"), caption="📋 requirements.txt")
                req_sent = True
        except Exception:
            pass
    if not req_sent:
        # MUHIM FIX: ilgari bu holatda hech narsa yuborilmasdi va hech qanday
        # xabar ham chiqmasdi — foydalanuvchi buni "yuklanmadi" (xato) deb
        # tushunishi mumkin edi. Endi aniq va ochiq xabar beriladi.
        await callback.message.answer("📋 requirements.txt yo'q (bot deploy qilinganda kiritilmagan yoki hali topilmadi).")

    if envs:
        try:
            env_file_text = "\n".join(f"{r['key']}={r['value']}" for r in envs)
            env_tmp_path = f"/tmp/bot_{bot_id}_env.txt"
            with open(env_tmp_path, "w", encoding="utf-8") as f:
                f.write(env_file_text)
            await bot.send_document(callback.from_user.id, FSInputFile(env_tmp_path, filename=".env"), caption="🔑 ENV")
            os.remove(env_tmp_path)
        except Exception:
            pass

    await callback.answer()


@router.callback_query(F.data.startswith("bot_resource:"))
async def cb_bot_resource(callback: CallbackQuery):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    if bot_row["status"] != "running" or not is_running(bot_id):
        await callback.answer("Bot hozir ishlamayapti — resurs sarfini ko'rsatib bo'lmaydi.", show_alert=True)
        return

    ram_mb = bot_ram_mb(bot_id)
    text = f"💾 Bu bot: {ram_mb:.1f} MB RAM"
    # Server umumiy RAM byudjeti (masalan "242/420 MB band") platformaning ichki
    # hisob-kitobi — oddiy foydalanuvchiga ko'rsatilmaydi, faqat superadmin uchun.
    if is_superadmin(callback.from_user.id):
        allowed, used_mb, budget_mb = can_start_new_bot()
        text += f"\nServer umumiy: {used_mb:.0f}/{budget_mb} MB band"
    await callback.answer(text, show_alert=True)


@router.callback_query(F.data.startswith("bot_live_log:"))
async def cb_bot_live_log(callback: CallbackQuery):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    await callback.answer()
    username = bot_row["bot_username"]
    label = f"@{username}" if username else (bot_row["display_name"] or f"Bot #{bot_id}")
    msg = await callback.message.answer(f"📡 {html.escape(label)} — live log (30 soniya yangilanadi)...")

    last_text = None
    # Render Free Tier'da CPU/RAM cheklangani uchun live log CHEKSIZ emas —
    # 30 soniya (har 3 soniyada bir yangilanish) bilan chegaralangan, keyin
    # to'xtaydi. Foydalanuvchi qayta tugmani bossa, yana 30 soniyaga yoqiladi.
    for _ in range(10):
        await asyncio.sleep(3)
        current_row = db.get_bot(bot_id)
        if current_row is None:
            break
        logs = read_log_tail(current_row["code_path"], n_lines=25)
        status_icon = "🟢" if is_running(bot_id) else "🔴"
        text = format_log_block(f"{label} {status_icon}", logs)
        if text != last_text:
            try:
                await msg.edit_text(text, parse_mode="HTML")
                last_text = text
            except Exception:
                pass  # matn o'zgarmagan bo'lsa Telegram xato qaytaradi, buni e'tiborsiz qoldiramiz

    try:
        final_text = (
            last_text + "\n\n<i>⏸ Yangilanish to'xtatildi. Davom ettirish uchun \"📡 Live log\"ni qayta bosing.</i>"
            if last_text else "Log topilmadi."
        )
        await msg.edit_text(final_text, parse_mode="HTML")
    except Exception:
        pass


# ---------- "🛠 Botni tahrirlash" menyusi (har qanday holatdagi bot uchun) ----------

@router.callback_query(F.data.startswith("edit_bot_menu:"))
async def cb_edit_bot_menu(callback: CallbackQuery):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    has_env = bool(db.list_envs(bot_id))
    bot_label = f"@{bot_row['bot_username']}" if bot_row["bot_username"] else (bot_row["display_name"] or f"Bot #{bot_id}")
    await callback.message.edit_text(
        f"🛠 <b>{html.escape(bot_label)}</b> — nimani almashtirmoqchisiz?\n\n"
        f"<i>Diqqat: o'zgarish saqlangach bot avtomatik qayta build qilinib, ishga tushiriladi "
        f"(agar hozir ishlab tursa, avval xavfsiz to'xtatiladi).</i>",
        parse_mode="HTML",
        reply_markup=edit_bot_menu_kb(bot_id, has_env=has_env, webhook_proxy_enabled=bool(bot_row["webhook_proxy_enabled"])),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rename_bot:"))
async def cb_rename_bot(callback: CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    await state.update_data(rename_bot_id=bot_id)
    await state.set_state(RenameBot.waiting_name)
    current = bot_row["display_name"] or bot_row["bot_username"] or "Nomsiz bot"
    await callback.message.answer(
        f"✏️ Hozirgi nom: <b>{html.escape(current)}</b>\n\nYangi nomni yozing (faqat ko'rsatiladigan "
        f"nom o'zgaradi, kod yoki bot_username o'zgarmaydi — qayta build kerak emas):",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(RenameBot.waiting_name)
async def receive_new_bot_name(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_id = data.get("rename_bot_id")
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != message.from_user.id and not is_admin(message.from_user.id)):
        await state.clear()
        await message.answer("Ruxsat yo'q yoki bot topilmadi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))
        return

    new_name = (message.text or "").strip()
    if not new_name:
        await message.answer("Nom bo'sh bo'lishi mumkin emas.")
        return
    if len(new_name) > 64:
        await message.answer("Nom juda uzun (maksimal 64 belgi). Qisqaroq yozing.")
        return

    db.set_bot_display_name(bot_id, new_name)
    await state.clear()

    bot_row = db.get_bot(bot_id)
    has_env = bool(db.list_envs(bot_id))
    await message.answer(
        f"✅ Nom o'zgartirildi: <b>{html.escape(new_name)}</b>",
        parse_mode="HTML",
        reply_markup=bot_manage_kb(bot_row, has_env=has_env, viewer_is_vip=is_admin(message.from_user.id)),
    )


# ---------- Crash-fix oqimi: kodni almashtirish ----------

@router.callback_query(F.data.startswith("fix_code:"))
async def cb_fix_code(callback: CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    await state.update_data(fix_bot_id=bot_id)
    await state.set_state(FixCode.waiting_file)
    await callback.message.answer(
        "📄 Yangi <b>.py</b> faylni yuboring — eski kod faqat yangi fayl muvaffaqiyatli "
        "qabul qilingandan SO'NG o'chiriladi (hozircha eski kod xavfsiz saqlanadi).\n\n"
        "Bekor qilish uchun pastdagi tugmani bosing.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(FixCode.waiting_file, F.document)
async def receive_fix_code_file(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    bot_id = data.get("fix_bot_id")
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != message.from_user.id and not is_admin(message.from_user.id)):
        await state.clear()
        await message.answer("Ruxsat yo'q yoki bot topilmadi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))
        return

    doc = message.document
    file_name = doc.file_name or ""
    lower_name = file_name.lower()

    # DIQQAT: ba'zi fayl-menejerlar (ayniqsa telefondan yuklaganda) faylni
    # "bot.py.txt" nomida saqlab yuborishi mumkin — bu foydalanuvchi xatosi emas,
    # balki fayl-menejerning "Save As" xatti-harakati. Shu holatni avtomatik
    # tuzatamiz: .txt qismini olib tashlab, asl .py nomiga qaytaramiz.
    if lower_name.endswith(".py.txt"):
        file_name = file_name[:-4]  # ".txt" ni kesib tashlaymiz -> "...py"
        lower_name = file_name.lower()

    if not lower_name.endswith(".py"):
        await message.answer(
            "❌ Faqat <b>.py</b> fayl qabul qilinadi. Boshqa kengaytmadagi fayl yubordingiz — "
            "iltimos, to'g'ri Python faylini yuboring yoki bekor qiling.",
            parse_mode="HTML",
        )
        return

    workdir = bot_row["code_path"]
    match = re.search(r'([^\s"\']+\.py)', bot_row["start_cmd"] or "")
    target_name = os.path.basename(match.group(1)) if match else "main.py"
    tmp_path = os.path.join(workdir, f"_incoming_{target_name}")

    try:
        await bot.download(doc, destination=tmp_path)
    except Exception as e:
        log.exception("Yangi kod faylini yuklab olishda xato")
        await message.answer(f"⚠️ Faylni yuklab olishda xato: {e}")
        return

    # Faqat shu yerda, yangi fayl MUVAFFAQIYATLI qabul qilingandan keyin eski
    # asosiy faylni o'chiramiz va yangisini o'rniga qo'yamiz — foydalanuvchi
    # noto'g'ri fayl yuborsa (yoki yuklashda tarmoq xatosi bo'lsa), eski kod
    # buzilmasdan qoladi.
    target_path = os.path.join(workdir, target_name)
    try:
        os.replace(tmp_path, target_path)
        fix_py_encoding(target_path)
    except Exception as e:
        log.exception("Yangi kodni joylashtirishda xato")
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        await message.answer(f"⚠️ Yangi kodni joylashtirishda xato: {e}")
        return

    await state.clear()

    # MUHIM FIX: yangi kodni ham asosiy deploy fayli bilan bir xil tamoyilda
    # STORAGE_GROUP_ID'ga backup qilamiz va DB'dagi storage_file_id'ni
    # yangilaymiz — aks holda platforma qayta ishga tushganda bot ESKI (ilk
    # deploydagi) kod bilan tiklanib qolardi.
    try:
        sent = await bot.send_document(
            STORAGE_GROUP_ID, doc.file_id,
            caption=f"{target_name} (tahrirlangan) — Bot #{bot_id}, Owner: {bot_row['owner_id']}",
        )
        db.set_storage_file_id(bot_id, sent.document.file_id, is_zip=False)
    except Exception:
        log.exception("Yangi kodni STORAGE_GROUP'ga backup qilishda xato")

    # Yangi kod bilan bevosita "qayta build + ishga tushirish" ni ishga tushiramiz —
    # foydalanuvchi yana alohida tugma bosishiga hojat qoldirmaslik uchun.
    await message.answer("✅ Yangi kod qabul qilindi. Qayta build va ishga tushirilmoqda...")
    await _rebuild_and_start(bot_id, message.bot, message)


@router.message(FixCode.waiting_file)
async def fix_code_wrong_content_type(message: Message):
    await message.answer("Iltimos, .py faylni <b>document</b> (fayl) sifatida yuboring.", parse_mode="HTML")


# ---------- Crash-fix oqimi: requirements.txt almashtirish ----------

@router.callback_query(F.data.startswith("webhook_proxy_on:"))
async def cb_webhook_proxy_on(callback: CallbackQuery, bot: Bot):
    """
    YANGI FEATURE: bot o'z ichida webhook-server kodiga ega bo'lsa (Flask/aiohttp
    va h.k.), shu yoqilgach botga PORT + RENDER_EXTERNAL_URL/WEBHOOK_HOST/WEBHOOK_URL
    avtomatik beriladi (start_bot_process ichida) — bot hosterbot'ning bitta umumiy
    porti ORQALI (proxy) chinakam webhook rejimida ishlay oladi.
    """
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    token = db.enable_webhook_proxy(bot_id)
    public_url = f"{WEBHOOK_BASE_URL.rstrip('/')}/PythonHosterRobot/{token}"
    await callback.message.answer(
        f"🌐 Webhook proxy yoqildi.\n\n"
        f"Bot manzili: <code>{public_url}</code>\n\n"
        f"Bot kodi ichida <code>RENDER_EXTERNAL_URL</code> yoki <code>WEBHOOK_HOST</code> "
        f"tekshirsa — avtomatik shu manzil beriladi. UptimeRobot yoki boshqa monitoring "
        f"uchun shu manzilga (kerak bo'lsa oxiriga botning o'z yo'lini qo'shib, masalan "
        f"<code>{public_url}/health</code>) ulanishingiz mumkin.\n\n"
        f"O'zgarish kuchga kirishi uchun bot qayta ishga tushirilmoqda...",
        parse_mode="HTML",
    )
    if bot_row["status"] == "running":
        await _rebuild_and_start(bot_id, bot, callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("webhook_proxy_off:"))
async def cb_webhook_proxy_off(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    db.disable_webhook_proxy(bot_id)
    await callback.message.answer("🌐 Webhook proxy o'chirildi. O'zgarish kuchga kirishi uchun bot qayta ishga tushirilmoqda...")
    if bot_row["status"] == "running":
        await _rebuild_and_start(bot_id, bot, callback.message)
    await callback.answer()


@router.callback_query(F.data.startswith("get_prev_data_backup:"))
async def cb_get_prev_data_backup(callback: CallbackQuery, bot: Bot):
    """
    YANGI FEATURE (xavfsizlik zaxirasi): agar botning ENG SO'NGGI data-backup'i
    biror sababdan yaroqsiz/bo'sh bo'lib chiqsa (masalan restart paytida hali
    birinchi backup ulgurmagan bo'lsa), foydalanuvchi shu tugma orqali BIR qadam
    OLDINGI snapshot'ni qo'lda olib, o'zi tekshirib ko'rishi mumkin.
    """
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    prev_id = bot_row["data_backup_file_id_prev"]
    if not prev_id:
        await callback.answer("Oldingi data backup topilmadi.", show_alert=True)
        return

    try:
        await bot.send_document(
            callback.from_user.id, prev_id,
            caption="🗄 Oldingi data backup (bir qadam avvalgi snapshot)",
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Xato: {e}", show_alert=True)


@router.callback_query(F.data.startswith("fix_reqs:"))
async def cb_fix_reqs(callback: CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    await state.update_data(fix_bot_id=bot_id)
    await state.set_state(FixRequirements.waiting_file)
    await callback.message.answer(
        "📋 Yangi <b>requirements.txt</b> faylni yuboring (masalan yetishmagan kutubxonani qo'shib).",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(FixRequirements.waiting_file, F.document)
async def receive_fix_reqs_file(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    bot_id = data.get("fix_bot_id")
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != message.from_user.id and not is_admin(message.from_user.id)):
        await state.clear()
        await message.answer("Ruxsat yo'q yoki bot topilmadi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))
        return

    doc = message.document
    file_name = (doc.file_name or "").lower()
    if file_name.endswith(".txt.txt"):
        file_name = file_name[:-4]
    if not file_name.endswith(".txt"):
        await message.answer("❌ Faqat .txt (requirements.txt) fayl qabul qilinadi.")
        return

    workdir = bot_row["code_path"]
    target_path = os.path.join(workdir, "requirements.txt")
    try:
        await bot.download(doc, destination=target_path)
    except Exception as e:
        log.exception("requirements.txt yuklashda xato")
        await message.answer(f"⚠️ Faylni yuklab olishda xato: {e}")
        return

    # MUHIM FIX: ilgari bu yerda yangi requirements.txt FAQAT workdir'ga
    # (Render'ning ephemeral diskiga) yozilardi — Telegram STORAGE_GROUP'ga hech
    # qachon backup qilinmasdi. Natijada platforma qayta ishga tushganda
    # (restart/redeploy) aynan shu "tuzatilgan" requirements.txt yo'qolib,
    # bot yana eski (yoki umuman yo'q) requirements.txt bilan tiklanardi.
    try:
        sent = await bot.send_document(
            STORAGE_GROUP_ID, doc.file_id,
            caption=f"requirements.txt (yangilangan) — Bot #{bot_id}, Owner: {bot_row['owner_id']}",
        )
        db.set_requirements_file_id(bot_id, sent.document.file_id)
    except Exception:
        log.exception("requirements.txt STORAGE_GROUP'ga backup qilishda xato")

    await state.clear()
    await message.answer("✅ Yangi requirements.txt qabul qilindi. Qayta build va ishga tushirilmoqda...")
    await _rebuild_and_start(bot_id, message.bot, message)


@router.message(FixRequirements.waiting_file)
async def fix_reqs_wrong_content_type(message: Message):
    await message.answer("Iltimos, requirements.txt faylni <b>document</b> (fayl) sifatida yuboring.", parse_mode="HTML")


async def _rebuild_and_start(bot_id: int, bot: Bot, message: Message):
    """fix_code/fix_reqs/fix_env oqimlaridan keyin umumiy qayta build+start logikasi —
    cb_bot_rebuild bilan bir xil ketma-ketlik, lekin callback emas, oddiy xabar
    orqali javob beradi (chunki bu yerda CallbackQuery yo'q, faqat Message).

    DIQQAT: bu funksiya endi FAQAT crashed botlar uchun emas, balki ishlab
    turgan (running) botni "🛠 Botni tahrirlash" orqali tahrirlaganda ham
    ishlatiladi. Agar bot hozir running bo'lsa, avval eskisini TO'XTATAMIZ —
    aks holda eski va yangi jarayon parallel ishlab, ikkitasi ham bitta
    Telegram token bilan polling qilib, konflikt (yoki ikki barobar RAM
    sarfi) yaratib qo'yishi mumkin edi."""
    bot_row = db.get_bot(bot_id)
    if bot_row is None:
        return
    bot_label = f"@{bot_row['bot_username']}" if bot_row["bot_username"] else (bot_row["display_name"] or f"Bot #{bot_id}")

    if bot_row["status"] == "running" and is_running(bot_id):
        await asyncio.to_thread(stop_bot_process, bot_id)

    allowed, used_mb, budget_mb = can_start_new_bot()
    if not allowed:
        await message.answer(format_ram_limit_message(message.from_user.id, used_mb, budget_mb, "Avval boshqa botni to'xtating."))
        return

    normalize_requirements_filename(bot_row["code_path"])
    fix_all_py_encodings(bot_row["code_path"])

    log_path = os.path.join(bot_row["code_path"], "run.log")
    try:
        with open(log_path, "a", encoding="utf-8") as log_file:
            build_ok = await asyncio.to_thread(run_build_command, bot_row["code_path"], bot_row["build_cmd"] or "", log_file)
    except Exception as e:
        log.exception("Qayta build qilishda xato")
        await message.answer(format_log_block(f"⚠️ {bot_label} — build xatosi", str(e)), parse_mode="HTML")
        return

    if not build_ok:
        db.set_bot_status(bot_id, "crashed", None)
        error_tail = read_log_tail(bot_row["code_path"], n_lines=40)
        await message.answer(format_log_block(f"❌ {bot_label} — build bosqichida yana xatolik", error_tail), parse_mode="HTML")
        return

    envs = {row["key"]: row["value"] for row in db.list_envs(bot_id)}
    try:
        pid = start_bot_process(bot_id, bot_row["code_path"], bot_row["start_cmd"], envs)
    except Exception as e:
        log.exception("start_bot_process xatoligi")
        await message.answer(format_log_block(f"⚠️ {bot_label} — ishga tushirib bo'lmadi", str(e)), parse_mode="HTML")
        return
    db.set_bot_status(bot_id, "running", pid)

    await asyncio.sleep(3)
    if not is_running(bot_id):
        db.set_bot_status(bot_id, "crashed", None)
        crash_log = read_log_tail(bot_row["code_path"], n_lines=40)
        await backup_database(bot)
        await message.answer(format_log_block(f"❌ {bot_label} — qayta ishga tushgach darhol qulab tushdi", crash_log), parse_mode="HTML")
        return

    bot_row = db.get_bot(bot_id)
    has_env = bool(db.list_envs(bot_id))
    await message.answer(
        f"✅ <b>{html.escape(bot_label)}</b> qayta build qilindi va ishlab turibdi!",
        parse_mode="HTML", reply_markup=bot_manage_kb(bot_row, has_env=has_env, viewer_is_vip=is_admin(bot_row["owner_id"])),
    )
    await backup_database(bot)


# ---------- Crash-fix oqimi: mavjud ENV qiymatlarini tahrirlash ----------

@router.callback_query(F.data.startswith("fix_env:"))
async def cb_fix_env(callback: CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    envs = db.list_envs(bot_id)
    if not envs:
        await callback.answer("Bu botda hozircha ENV yo'q.", show_alert=True)
        return

    env_list = "\n".join(f"• <code>{html.escape(e['key'])}</code>" for e in envs)
    await state.update_data(fix_bot_id=bot_id)
    await state.set_state(FixEnv.waiting_key_value)
    await callback.message.answer(
        f"🔑 Mavjud ENV kalitlari:\n{env_list}\n\n"
        f"Yangilamoqchi bo'lgan qiymatni <code>KEY=YANGI_QIYMAT</code> shaklida yuboring "
        f"(bir vaqtda bitta ENV).",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(FixEnv.waiting_key_value)
async def receive_fix_env_value(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    bot_id = data.get("fix_bot_id")
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != message.from_user.id and not is_admin(message.from_user.id)):
        await state.clear()
        await message.answer("Ruxsat yo'q yoki bot topilmadi.", reply_markup=main_menu_kb(is_admin=is_admin(message.from_user.id)))
        return

    text = (message.text or "").strip()
    if "=" not in text:
        await message.answer("Format noto'g'ri. <code>KEY=QIYMAT</code> shaklida yuboring.", parse_mode="HTML")
        return

    key, _, value = text.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        await message.answer("KEY bo'sh bo'lishi mumkin emas.")
        return

    existing_keys = {e["key"] for e in db.list_envs(bot_id)}
    if key not in existing_keys:
        await message.answer(
            f"⚠️ <code>{html.escape(key)}</code> bu botda mavjud emas. Faqat mavjud ENV kalitlarini "
            f"tahrirlash mumkin (yangi ENV qo'shish uchun botni qayta deploy qiling).",
            parse_mode="HTML",
        )
        return

    db.upsert_env(bot_id, key, value)
    write_env_file(bot_id, {e["key"]: e["value"] for e in db.list_envs(bot_id)})
    await state.clear()

    await message.answer(f"✅ <code>{html.escape(key)}</code> yangilandi. Qayta build va ishga tushirilmoqda...", parse_mode="HTML")
    await _rebuild_and_start(bot_id, message.bot, message)


# ---------- AI yordam (crash-tashxis, Stars orqali to'lanadi) ----------

@router.callback_query(F.data.startswith("ai_help:"))
async def cb_ai_help(callback: CallbackQuery, bot: Bot):
    bot_id = int(callback.data.split(":")[1])
    bot_row = db.get_bot(bot_id)
    if not _authorized(callback, bot_row):
        await callback.answer("Ruxsat yo'q.", show_alert=True)
        return

    price = 0 if is_admin(callback.from_user.id) else db.get_ai_help_price_stars()
    owner_id = bot_row["owner_id"]
    balance = db.get_user_balance(owner_id)
    if balance < price:
        await callback.answer(
            f"⭐️ Balansingiz yetarli emas ({balance}/{price}). \"💳 Hisob\" orqali to'ldiring.",
            show_alert=True,
        )
        return

    await callback.answer("🤖 AI tahlil qilmoqda, biroz kuting...")
    bot_label = f"@{bot_row['bot_username']}" if bot_row["bot_username"] else (bot_row["display_name"] or f"Bot #{bot_id}")
    thinking_msg = await callback.message.answer(f"🤖 {html.escape(bot_label)} uchun AI tashxis tayyorlanmoqda...")

    log_text = read_log_tail(bot_row["code_path"], n_lines=60)
    code_snippet = ""
    try:
        match = re.search(r'([^\s"\']+\.py)', bot_row["start_cmd"] or "")
        py_name = os.path.basename(match.group(1)) if match else None
        if py_name:
            py_path = os.path.join(bot_row["code_path"], py_name)
            if os.path.isfile(py_path):
                with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
                    code_snippet = f.read()
    except Exception:
        pass  # kod parchasini o'qib bo'lmasa ham, log bilan tashxis qo'yishga urinamiz

    # requirements.txt ham AI'ga beriladi — aks holda AI "kutubxona yo'q" desa
    # ham, o'sha kutubxona aslida requirements.txt'da bor-yo'qligini bilmasdan
    # taxmin qilardi (masalan nom xato yozilgan yoki versiya nomuvofiqligi
    # bo'lgan holatlarda noto'g'ri tashxis qo'yishi mumkin edi).
    requirements_text = ""
    try:
        requirements_path = os.path.join(bot_row["code_path"], "requirements.txt")
        if os.path.isfile(requirements_path):
            with open(requirements_path, "r", encoding="utf-8", errors="ignore") as f:
                requirements_text = f.read()
    except Exception:
        pass

    system_prompt, user_prompt = build_crash_diagnosis_prompt(bot_label, log_text, code_snippet, requirements_text)

    try:
        diagnosis = await ask_ai(system_prompt, user_prompt, telegram_id=owner_id, bot_id=bot_id)
    except AIError as e:
        log.warning(f"AI yordam xatosi (bot_id={bot_id}): {e}")
        await thinking_msg.edit_text(
            f"⚠️ AI yordam hozircha ishlamayapti: {html.escape(str(e))}\n\n"
            f"Stars balansingizdan hech narsa yechilmadi."
        )
        return

    if price > 0:
        # Faqat AI muvaffaqiyatli javob qaytargandan KEYIN Stars yechamiz —
        # foydalanuvchi ishlamagan xizmat uchun pul to'lamasligi kerak.
        db.add_user_balance(owner_id, -price, reason=f"AI crash-tashxis (bot #{bot_id})")
        new_balance = db.get_user_balance(owner_id)

        # MUHIM FIX: Stars yechilgandan keyin DARHOL backup qilamiz (boshqa barcha
        # balans/bot holatini o'zgartiruvchi joylar kabi). Aks holda: Render Free
        # Tier diski ephemeral (uxlab-uyg'onishda yoki qayta ishga tushishda
        # o'chadi) — agar server shu balans o'zgarishidan KEYIN, lekin keyingi
        # backup'dan OLDIN qayta ko'tarilsa, restore_database() eski (Stars hali
        # yechilmagan) backup'ni tiklab qo'yardi va foydalanuvchi Stars sarflab,
        # baribir balansi o'zgarmagan holatga tushib qolardi (aynan shu bug
        # ko'zga tashlangan holat edi).
        await backup_database(bot)
        price_note = f"\n\n<i>-{price}⭐️ yechildi. Qolgan balans: {new_balance}⭐️</i>"
    else:
        # Admin/superadmin uchun AI — VIP, bepul: balans tekshiruvi/yechish/backup
        # umuman chaqirilmaydi (keraksiz DB yozuv yoki ledger yozuvi yaratmaslik uchun).
        price_note = "\n\n<i>🎁 VIP (admin) — bepul.</i>"

    await thinking_msg.edit_text(
        f"🤖 <b>AI tashxis — {html.escape(bot_label)}</b>\n\n{html.escape(diagnosis)}{price_note}",
        parse_mode="HTML",
    )
