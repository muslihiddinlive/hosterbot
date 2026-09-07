import os
import re
import shutil
import html
import asyncio
import json

from aiogram import Router, F, Bot
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

import database as db
from config import MAX_BOTS_PER_USER, STORAGE_GROUP_ID, SUPERADMIN_IDS, ADMIN_IDS, is_admin
from states import AddBot
from keyboards import cancel_kb, skip_or_add_env_kb, env_added_kb, main_menu_kb, skip_requirements_kb, auto_build_kb
from services.file_utils import (
    detect_language_from_zip, extract_zip, bot_workdir, write_env_file,
    resolve_project_root, find_requirements_txt, normalize_requirements_filename,
    list_py_files_in_zip, resolve_start_command, fix_all_py_encodings,
    detect_external_imports, detect_credentials,
)
from services.deploy_manager import run_build_command, start_bot_process, static_scan, read_log_tail, is_running, format_log_block
from services.resource_monitor import can_start_new_bot, format_ram_limit_message
from services.backup import backup_database
from handlers.stars import format_remaining
import time

router = Router()

ONLY_PYTHON_TEXT = (
    "⚠️ <b>Faqat Python botlar yasay olasiz.</b>\n"
    "Boshqa til qo'shmoqchi bo'lsangiz, adminga murojaat qiling — u kodni o'zgartiradi!!"
)


@router.message(F.text == "➕ Bot qo'shish")
async def add_bot_start(message: Message, state: FSMContext):
    if db.is_banned(message.from_user.id):
        await message.answer("⛔️ Sizning ruxsatingiz olib tashlangan. Bot host qila olmaysiz.")
        return

    if not is_admin(message.from_user.id):
        approved = db.is_user_approved(message.from_user.id)
        balance = db.get_user_balance(message.from_user.id)
        stars_per_unit = db.get_stars_per_unit()
        seconds_per_unit = db.get_seconds_per_unit()
        if not approved and balance < stars_per_unit:
            await message.answer(
                f"Bot host qilish uchun 2 ta yo'l bor:\n\n"
                f"1️⃣ Admin tomonidan tasdiqlanish — \"📩 Adminga habar berish\"\n"
                f"2️⃣ O'zingiz Stars orqali to'lab, darhol host qilish — \"💳 Hisob\" "
                f"(kamida {stars_per_unit} ⭐️ kerak, bu {seconds_per_unit // 3600} soatlik hosting)"
            )
            return

        max_bots = db.get_user_max_bots(message.from_user.id)
        limit = max_bots if max_bots is not None else MAX_BOTS_PER_USER
        current = db.count_user_bots(message.from_user.id)
        if current >= limit:
            await message.answer(
                f"Siz maksimal ruxsat etilgan bot sonidan ({limit}) foydalanib bo'ldingiz. "
                f"Yangi bot qo'shish uchun avval birortasini o'chiring."
            )
            return

    await state.clear()
    await state.set_state(AddBot.waiting_code)
    await message.answer(
        ONLY_PYTHON_TEXT + "\n\nBotingiz kodini <b>.py</b> fayl yoki <b>.zip</b> arxiv ko'rinishida yuboring.",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(AddBot.waiting_code, F.document)
async def receive_code(message: Message, state: FSMContext, bot: Bot):
    doc = message.document
    file_name = doc.file_name or "uploaded"
    is_zip = file_name.lower().endswith(".zip")
    is_py = file_name.lower().endswith(".py")

    if not (is_zip or is_py):
        await message.answer("Faqat .py yoki .zip fayl yuboring.")
        return

    tmp_dir = f"/tmp/upload_{message.from_user.id}_{message.message_id}"
    os.makedirs(tmp_dir, exist_ok=True)
    local_path = os.path.join(tmp_dir, file_name)
    await bot.download(doc, destination=local_path)

    external_imports: list[str] = []
    if is_py:
        language = "python"
        with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
            code_text = f.read()
        warnings = static_scan(code_text)
        external_imports = detect_external_imports(code_text)
    else:
        language = detect_language_from_zip(local_path)
        warnings = []

    if language != "python":
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await message.answer(ONLY_PYTHON_TEXT, parse_mode="HTML")
        return

    # STORAGE_GROUP_ID guruhiga backup sifatida yuboramiz (disk ephemeral bo'lgani uchun)
    storage_file_id = None
    try:
        sent = await bot.send_document(
            STORAGE_GROUP_ID, doc.file_id,
            caption=f"Owner: {message.from_user.id} (@{message.from_user.username})\nFile: {file_name}",
        )
        storage_file_id = sent.document.file_id
    except Exception:
        pass

    await state.update_data(
        tmp_path=local_path, tmp_dir=tmp_dir, file_name=file_name,
        is_zip=is_zip, language=language, storage_file_id=storage_file_id,
        external_imports=external_imports,
    )

    warn_text = ""
    if warnings:
        warn_text = (
            "\n\n⚠️ Kodingizda diqqat talab qiladigan qatorlar aniqlandi, admin buni ko'rib chiqadi."
        )

    if is_zip:
        py_files = list_py_files_in_zip(local_path)
        if py_files:
            files_list = "\n".join(f"• <code>{html.escape(p)}</code>" for p in py_files)
            files_hint = (
                f"\n\n📄 Arxivda topilgan .py fayllar (start buyrug'ida <b>aynan shu nomni</b>, "
                f"bo'shliq bo'lsa qo'shtirnoq bilan yozing):\n{files_list}"
            )
        else:
            files_hint = "\n\n⚠️ Arxivda .py fayl topilmadi — start buyrug'i ishlamasligi mumkin."
    else:
        files_hint = (
            f"\n\n📄 Fayl nomi: <code>{html.escape(file_name)}</code> "
            f"(start buyrug'ida aynan shu nomni, kerak bo'lsa qo'shtirnoq bilan yozing)"
        )

    if is_py:
        await state.set_state(AddBot.waiting_requirements)
        if external_imports:
            libs_list = ", ".join(f"<code>{html.escape(lib)}</code>" for lib in external_imports)
            deps_text = (
                f"\n\n📦 Kodingizda quyidagi tashqi kutubxonalar ishlatilgani aniqlandi: {libs_list}\n"
                f"Bular avtomatik o'rnatilishi uchun <b>requirements.txt</b> faylini yuboring "
                f"(yoki \"O'tkazib yuborish\"ni bossangiz, bot shu kutubxonalarsiz ishga tushiriladi va "
                f"<code>ModuleNotFoundError</code> bilan qulashi mumkin)."
            )
        else:
            deps_text = (
                "\n\nAgar botingiz tashqi kutubxonalar ishlatsa (masalan <code>aiogram</code>, <code>requests</code>, "
                "<code>python-telegram-bot</code> va h.k.), endi <b>requirements.txt</b> faylini yuboring.\n"
                "Agar tashqi kutubxona kerak bo'lmasa (faqat standart Python), pastdagi tugmani bosing."
            )
        await message.answer(
            "✅ Kod qabul qilindi." + warn_text + files_hint + deps_text,
            parse_mode="HTML",
            reply_markup=skip_requirements_kb(),
        )
        return

    await state.set_state(AddBot.waiting_build_cmd)
    await message.answer(
        "✅ Kod qabul qilindi." + warn_text + files_hint + "\n\n"
        "Build buyrug'i odatda <b>avtomatik</b> aniqlanadi (arxivda requirements.txt topilsa, "
        "o'zi <code>pip install -r requirements.txt</code> qiladi). Pastdagi tugmani bosing (tavsiya etiladi), "
        "yoki maxsus buyruq kerak bo'lsa yozing.",
        parse_mode="HTML",
        reply_markup=auto_build_kb(),
    )


@router.message(AddBot.waiting_code)
async def receive_code_invalid(message: Message):
    await message.answer("Iltimos, .py yoki .zip faylni <b>fayl (document)</b> sifatida yuboring.", parse_mode="HTML")


@router.message(AddBot.waiting_requirements, F.document)
async def receive_requirements(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    tmp_dir = data.get("tmp_dir")
    req_path = os.path.join(tmp_dir, "requirements.txt")
    await bot.download(message.document, destination=req_path)
    await state.update_data(requirements_path=req_path)

    await state.set_state(AddBot.waiting_build_cmd)
    await message.answer(
        "✅ requirements.txt qabul qilindi.\n\n"
        "Build buyrug'i endi avtomatik: <code>pip install -r requirements.txt</code>. "
        "Pastdagi tugmani bosing (tavsiya etiladi), yoki boshqacha buyruq kerak bo'lsa yozing.",
        parse_mode="HTML",
        reply_markup=auto_build_kb(),
    )


@router.message(AddBot.waiting_requirements, F.text == "➡️ O'tkazib yuborish (kerak emas)")
async def skip_requirements(message: Message, state: FSMContext):
    data = await state.get_data()
    external_imports = data.get("external_imports") or []
    await state.set_state(AddBot.waiting_build_cmd)

    if external_imports:
        libs_list = ", ".join(f"<code>{html.escape(lib)}</code>" for lib in external_imports)
        await message.answer(
            f"⚠️ <b>Diqqat:</b> kodingizda {libs_list} kabi tashqi kutubxonalar borligi aniqlangan edi, "
            f"lekin requirements.txt o'tkazib yuborildi. Bot ishga tushganda "
            f"<code>ModuleNotFoundError</code> bilan qulashi mumkin.\n\n"
            f"Davom etsangiz, keyinroq \"Mening botlarim\" bo'limidan botni tahrirlab, "
            f"requirements.txt qo'shishingiz mumkin.",
            parse_mode="HTML",
        )

    await message.answer(
        "Yaxshi, tashqi kutubxonasiz davom etamiz.\n\n"
        "Build buyrug'i kerak bo'lmaydi. Pastdagi tugmani bosing (tavsiya etiladi), "
        "yoki maxsus buyruq kerak bo'lsa yozing.",
        parse_mode="HTML",
        reply_markup=auto_build_kb(),
    )


@router.message(AddBot.waiting_requirements)
async def receive_requirements_invalid(message: Message):
    await message.answer(
        "Iltimos, <b>requirements.txt</b> faylini fayl (document) sifatida yuboring, "
        "yoki pastdagi tugmani bosing.",
        parse_mode="HTML",
    )


@router.message(AddBot.waiting_build_cmd, F.text == "✅ Avtomatik (tavsiya etiladi)")
async def auto_build_cmd(message: Message, state: FSMContext):
    await state.update_data(build_cmd="__AUTO__")
    await state.set_state(AddBot.waiting_start_cmd)
    await message.answer(
        "Endi <b>start buyrug'ini</b> yozing (masalan: <code>python bot.py</code>):",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(AddBot.waiting_build_cmd)
async def receive_build_cmd(message: Message, state: FSMContext):
    build_cmd = "" if message.text.strip() == "-" else message.text.strip()
    await state.update_data(build_cmd=build_cmd)
    await state.set_state(AddBot.waiting_start_cmd)
    await message.answer(
        "Endi <b>start buyrug'ini</b> yozing (masalan: <code>python bot.py</code>):",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(AddBot.waiting_start_cmd)
async def receive_start_cmd(message: Message, state: FSMContext):
    start_cmd = message.text.strip()
    await state.update_data(start_cmd=start_cmd, envs={})
    await state.set_state(AddBot.waiting_env)
    await message.answer(
        "Endi ENV o'zgaruvchilarini yuboring.\n\n"
        "Format: <code>KEY - VALUE</code> (har bir ENV alohida xabar sifatida)\n"
        "Masalan: <code>BOT_TOKEN - 123456:AAAए...</code>\n\n"
        "ENV shart emas — agar kerak bo'lmasa, pastdagi tugmani bosing.",
        parse_mode="HTML",
        reply_markup=skip_or_add_env_kb(),
    )


@router.message(AddBot.waiting_env, F.text == "➡️ O'tkazib yuborish (ENV kerak emas)")
async def skip_env(message: Message, state: FSMContext, bot: Bot):
    await finalize_deploy(message, state, bot)


@router.message(AddBot.waiting_env, F.text == "➕ Yana ENV qo'shish")
async def add_more_env(message: Message, state: FSMContext):
    await message.answer(
        "Keyingi ENV ni yuboring, format: <code>KEY - VALUE</code>",
        parse_mode="HTML",
        reply_markup=skip_or_add_env_kb(),
    )


@router.message(AddBot.waiting_env, F.text == "✅ Tugatish va deploy qilish")
async def finish_env(message: Message, state: FSMContext, bot: Bot):
    await finalize_deploy(message, state, bot)


@router.message(AddBot.waiting_env)
async def receive_env_pair(message: Message, state: FSMContext):
    text = message.text.strip()
    if "-" in text:
        key, _, value = text.partition("-")
    elif "=" in text:
        key, _, value = text.partition("=")
    else:
        await message.answer(
            "Noto'g'ri format. Iltimos: <code>KEY - VALUE</code>\n"
            "Masalan: <code>BOT_TOKEN - 123456:AAExample-Token</code>",
            parse_mode="HTML",
        )
        return

    key, value = key.strip(), value.strip()

    # ENV kaliti odatda "BOT_TOKEN", "ADMIN_IDS" kabi ENV_NOM ko'rinishida bo'ladi.
    # Agar foydalanuvchi "KEY - " qismini kiritmasdan, faqat qiymatning o'zini
    # yuborsa (masalan token ichida "-" bo'lgani uchun), yuqoridagi partition() shu
    # qiymatning o'zini "kalit" deb noto'g'ri bo'lib yuboradi — natijada kerakli
    # ENV o'zgaruvchisi (masalan BOT_TOKEN) HECH QACHON o'rnatilmay qoladi va bot
    # keyinchalik tushunarsiz xato bilan ishlamay qoladi. Shu sabab kalitni tekshirib,
    # ENV o'zgaruvchisiga o'xshamasa (bo'sh, juda uzun, yoki harf bilan boshlanmasa)
    # aniq ogohlantirib, qayta so'raymiz.
    if not key or not value or len(key) > 64 or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        await message.answer(
            f"⚠️ Kalit (<code>{html.escape(key) or '—'}</code>) ENV o'zgaruvchisiga o'xshamayapti "
            f"(odatda <code>BOT_TOKEN</code>, <code>ADMIN_IDS</code> kabi, faqat harf/raqam/pastki chiziq).\n\n"
            f"Ehtimol siz \"<code>KEY - </code>\" qismini yozmay, faqat qiymatning o'zini yubordingiz.\n"
            f"Iltimos qaytadan, aniq shu formatda yozing: <code>BOT_TOKEN - {html.escape(value) or 'qiymat'}</code>",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    envs = data.get("envs", {})
    envs[key] = value
    await state.update_data(envs=envs)

    await message.answer(
        f"✅ Qo'shildi: <code>{key}</code>\n\nYana qo'shasizmi yoki tugatasizmi?",
        parse_mode="HTML",
        reply_markup=env_added_kb(),
    )


async def finalize_deploy(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()

    owner_id = message.from_user.id
    deployed_by = owner_id  # hozircha faqat o'z nomidan deploy qilish mavjud; kelajakda admin
    # boshqa foydalanuvchi nomidan deploy qilsa, shu yerga o'sha admin id'si yoziladi

    display_name = os.path.splitext(data.get("file_name", ""))[0] or None

    bot_id = db.create_bot(
        owner_id=owner_id,
        bot_username=None,
        bot_token=None,
        code_path="",  # pastda to'ldiriladi
        storage_file_id=data.get("storage_file_id"),
        is_zip=data.get("is_zip", False),
        language=data.get("language", "python"),
        build_cmd=data.get("build_cmd", ""),
        start_cmd=data.get("start_cmd", ""),
        display_name=display_name,
        deployed_by=deployed_by,
    )

    extract_dir = bot_workdir(bot_id)
    tmp_path = data["tmp_path"]

    if data.get("is_zip"):
        extract_zip(tmp_path, extract_dir)
        # Ko'p zip'lar ichida bitta "wrapper" papka bo'ladi (masalan loyiha-main/...).
        # Haqiqiy build/start shu ichki papkadan ishga tushishi kerak.
        workdir = resolve_project_root(extract_dir)
    else:
        workdir = extract_dir
        shutil.copy(tmp_path, os.path.join(workdir, os.path.basename(tmp_path)))
        # .py yuklashda alohida yuborilgan requirements.txt bo'lsa, shu yerga qo'shamiz
        req_path = data.get("requirements_path")
        if req_path and os.path.exists(req_path):
            shutil.copy(req_path, os.path.join(workdir, "requirements.txt"))

    shutil.rmtree(data.get("tmp_dir", ""), ignore_errors=True)

    # requirements.txt.txt kabi noto'g'ri nomlangan fayllarni avtomatik tuzatamiz
    normalize_requirements_filename(workdir)

    # UTF-8 bo'lmagan .py fayllarni avtomatik tuzatamiz (Windows'da cp1251/cp1252
    # bilan saqlangan fayllar tez-tez uchraydi va SyntaxError beradi)
    encoding_report = fix_all_py_encodings(workdir)
    if encoding_report["failed"]:
        failed_list = "\n".join(f"• <code>{html.escape(f)}</code>" for f in encoding_report["failed"])
        await message.answer(
            f"❌ Quyidagi fayl(lar)ning matn kodировkasini aniqlab bo'lmadi (fayl buzilgan bo'lishi mumkin):\n\n"
            f"{failed_list}\n\n"
            f"Faylni matn muharriringizda oching va <b>UTF-8</b> formatida qayta saqlang, so'ng qaytadan yuklang.",
            parse_mode="HTML",
            reply_markup=main_menu_kb(is_admin=is_admin(owner_id)),
        )
        cleanup_bot_files(bot_id)
        db.delete_bot(bot_id)
        return
    if encoding_report["fixed"]:
        fixed_list = "\n".join(f"• <code>{html.escape(f)}</code> ({enc})" for f, enc in encoding_report["fixed"])
        await message.answer(
            f"ℹ️ Quyidagi fayl(lar) UTF-8 emas edi, avtomatik tuzatildi:\n{fixed_list}",
            parse_mode="HTML",
        )

    envs = data.get("envs", {})
    for k, v in envs.items():
        db.add_env(bot_id, k, v)
    if envs:
        write_env_file(bot_id, envs)

    build_cmd = data.get("build_cmd", "")

    if build_cmd == "__AUTO__":
        # Avtomatik: requirements.txt topilsa o'shani o'rnatamiz, topilmasa hech narsa qilmaymiz
        found = find_requirements_txt(workdir)
        if found:
            rel_path = os.path.relpath(found, workdir)
            build_cmd = f"pip install -r {rel_path}" if rel_path != "requirements.txt" else "pip install -r requirements.txt"
        else:
            build_cmd = ""
    elif "requirements.txt" in build_cmd and not os.path.exists(os.path.join(workdir, "requirements.txt")):
        # Foydalanuvchi qo'lda "pip install -r requirements.txt" yozgan-u, fayl boshqa
        # (masalan chuqurroq) joyda bo'lsa, yo'lni avtomatik topib to'g'irlaymiz.
        found = find_requirements_txt(workdir)
        if found:
            rel_path = os.path.relpath(found, workdir)
            build_cmd = build_cmd.replace("requirements.txt", rel_path)

    start_cmd = resolve_start_command(data.get("start_cmd", ""), workdir)

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE bots SET code_path=?, build_cmd=?, start_cmd=? WHERE bot_id=?",
            (workdir, build_cmd, start_cmd, bot_id),
        )

    await message.answer("⏳ Bot deploy qilinmoqda...", reply_markup=main_menu_kb(is_admin=is_admin(owner_id)))

    log_path = os.path.join(workdir, "run.log")
    with open(log_path, "w", encoding="utf-8") as log_file:
        build_ok = await asyncio.to_thread(run_build_command, workdir, build_cmd, log_file)

    if not build_ok:
        db.set_bot_status(bot_id, "crashed")
        await backup_database(bot)
        error_tail = read_log_tail(workdir, n_lines=40)
        bot_row_now = db.get_bot(bot_id)
        label = f"@{bot_row_now['bot_username']}" if bot_row_now["bot_username"] else (bot_row_now["display_name"] or f"Bot #{bot_id}")
        await message.answer(
            format_log_block(f"❌ {label} — build bosqichida xatolik", error_tail),
            parse_mode="HTML",
        )
        return

    # Serverning umumiy fizik RAM byudjetiga sig'ish-sig'masligini tekshiramiz —
    # build muvaffaqiyatli bo'lsa ham, RAM yetmasa yangi botni ishga tushirmaymiz
    # (aks holda Render OOM-kill qilib, boshqa aybsiz botlarni ham ag'darib
    # yuborishi mumkin edi).
    allowed, used_mb, budget_mb = can_start_new_bot()
    if not allowed:
        db.set_bot_status(bot_id, "stopped")
        await backup_database(bot)
        ram_note = format_ram_limit_message(message.from_user.id, used_mb, budget_mb)
        await message.answer(
            f"⚠️ <b>Bot build bo'ldi, lekin hozircha ishga tushirilmadi.</b>\n\n"
            f"{ram_note} Boshqa botni to'xtatib, keyin \"Mening botlarim\" bo'limidan qo'lda "
            f"ishga tushirishingiz mumkin.",
            parse_mode="HTML",
        )
        return

    # Admin tasdiqisiz (self-service) deploy qilayotgan bo'lsa — Stars balansidan
    # yechamiz va shu botga 24 soatlik (yoki sozlangan) hosting huquqi beramiz.
    # Balansni QAYTA tekshiramiz (race condition himoyasi: masalan build paytida
    # boshqa oynada balansni sarflab qo'ygan yoki ban qilingan bo'lishi mumkin).
    if db.is_banned(owner_id):
        db.set_bot_status(bot_id, "stopped")
        await backup_database(bot)
        await message.answer("⛔️ Ruxsatingiz olib tashlangan, deploy bekor qilindi.")
        return

    stars_flow = not db.is_user_approved(owner_id)
    seconds_per_unit = db.get_seconds_per_unit()
    if stars_flow:
        stars_per_unit = db.get_stars_per_unit()
        balance = db.get_user_balance(owner_id)
        if balance < stars_per_unit:
            db.set_bot_status(bot_id, "stopped")
            await backup_database(bot)
            await message.answer(
                f"⚠️ Build muvaffaqiyatli bo'ldi, lekin balansingiz yetarli emas ({balance}/{stars_per_unit} ⭐️). "
                f"\"💳 Hisob\" orqali to'ldiring, so'ng \"Mening botlarim\"dan ishga tushiring.",
            )
            return
        db.add_user_balance(owner_id, -stars_per_unit, reason=f"Self-service deploy (bot #{bot_id})")
        paid_until = int(time.time()) + seconds_per_unit
        db.set_bot_stars_payment(bot_id, paid_until)

    pid = start_bot_process(bot_id, workdir, start_cmd, envs)
    db.set_bot_status(bot_id, "running", pid)

    # Ba'zi xatolar (noto'g'ri token, import xatosi va h.k.) faqat process ishga
    # tushgandan keyin, bir necha soniya ichida chiqadi — shuning uchun "deploy
    # bo'ldi" deb aytishdan oldin process haqiqatan tirikligini tekshiramiz.
    await asyncio.sleep(3)
    if not is_running(bot_id):
        db.set_bot_status(bot_id, "crashed", None)
        crash_log = read_log_tail(workdir, n_lines=40)
        await backup_database(bot)
        bot_row_now = db.get_bot(bot_id)
        label = f"@{bot_row_now['bot_username']}" if bot_row_now["bot_username"] else (bot_row_now["display_name"] or f"Bot #{bot_id}")
        await message.answer(
            format_log_block(f"❌ {label} — ishga tushirilgach darhol qulab tushdi", crash_log),
            parse_mode="HTML",
        )
        return

    # Kod ichidan token/chat_id'larni avtomatik qidiramiz (ENV orqali berilmagan
    # bo'lsa ham) — admin uchun shaffoflik va username'ni aniqlash uchun kerak.
    try:
        detected = detect_credentials(workdir)
        if detected:
            db.set_bot_detected_credentials(bot_id, json.dumps(detected))
    except Exception:
        detected = []

    # Token bo'lsa, bot username'ni aniqlashga urinib ko'ramiz — avval ENV'dan,
    # topilmasa kod ichidan avtomatik aniqlangan tokendan.
    token_value = None
    for k, v in envs.items():
        if "TOKEN" in k.upper():
            token_value = v
            break
    if not token_value:
        token_hit = next((c for c in detected if c["type"] == "token"), None)
        if token_hit:
            token_value = token_hit["value"]

    username_text = ""
    if token_value:
        try:
            temp_bot = Bot(token=token_value)
            me = await temp_bot.get_me()
            db.set_bot_username(bot_id, me.username)
            username_text = f"\n🤖 Bot username: @{me.username}"
            await temp_bot.session.close()
        except Exception:
            pass

    stars_text = ""
    if stars_flow:
        stars_text = f"\n⭐️ Stars orqali hostlandi — qolgan vaqt: {format_remaining(seconds_per_unit)}"

    await message.answer(f"✅ <b>Bot deploy bo'ldi va ishlab turibdi!</b>{username_text}{stars_text}", parse_mode="HTML")
    await backup_database(bot)

    notify_text = (
        f"🆕 <b>Yangi bot deploy qilindi</b>\n"
        f"👤 Owner: {html.escape(message.from_user.full_name)} (ID: <code>{owner_id}</code>)"
        f"{username_text}"
    )
    for admin_id in (ADMIN_IDS | SUPERADMIN_IDS):
        if admin_id == owner_id:
            continue
        try:
            await bot.send_message(admin_id, notify_text, parse_mode="HTML")
        except Exception:
            continue
