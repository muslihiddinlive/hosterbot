import asyncio
import html
import logging
import os
import re
import shutil
import time

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import BOT_TOKEN, WEBHOOK_BASE_URL, WEBHOOK_PATH, PORT, ADMIN_IDS, SUPERADMIN_IDS, is_admin
import database as db
from services.backup import restore_database, backup_database
from services.data_backup import backup_bot_data, restore_bot_data, _workdir_signature
from services.deploy_manager import is_running, read_log_tail, run_build_command, start_bot_process, stop_bot_process, format_log_block
from services.file_utils import (
    bot_workdir, extract_zip, resolve_project_root,
    normalize_requirements_filename, fix_all_py_encodings,
)
from keyboards import crash_notify_kb

from services.github_deploy import download_repo_zip, GitHubDeployError
from handlers.bot_actions import _rebuild_and_start

from handlers import start, admin_review, user_menu, add_bot, bot_actions, admin_panel, stars, help_faq, ai_chat, github_deploy

WATCHDOG_INTERVAL_SEC = 60
BILLING_WATCHDOG_INTERVAL_SEC = 30
STAR_BALANCE_WATCHDOG_INTERVAL_SEC = 300
STAR_BALANCE_ALERT_THRESHOLD = 1000
DATA_CHANGE_CHECK_INTERVAL_SEC = 1   # workdir har necha soniyada tekshiriladi (yengil, tez)
DATA_CHANGE_DEBOUNCE_SEC = 2         # o'zgarish sezilgandan necha soniya keyin backup qilinadi

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hosterbot")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

dp.include_router(start.router)
dp.include_router(admin_review.router)
dp.include_router(user_menu.router)
dp.include_router(add_bot.router)
dp.include_router(bot_actions.router)
dp.include_router(admin_panel.router)
dp.include_router(stars.router)
dp.include_router(help_faq.router)
dp.include_router(github_deploy.router)
dp.include_router(ai_chat.router)


@dp.errors()
async def global_error_handler(event: ErrorEvent):
    """
    Har qanday handler'da (aynan try/except bilan o'ralmagan joyda) yuz beradigan
    kutilmagan xatoni ushlab qoladi — aks holda foydalanuvchi hech narsa ko'rmay,
    xato faqat Render loglarida (yoki umuman hech qayerda) qolib ketardi.
    Bosilgan tugma "ishlamayapti" kabi ko'rinishining asosiy sababi shu edi.
    """
    log.exception(f"Ushlanmagan xato: {event.exception}")
    update = event.update
    err_text = html.escape(str(event.exception))[:500]
    try:
        if update.callback_query:
            # Alert (show_alert) matnini ko'chirib bo'lmaydi — shu sabab haqiqiy
            # chat xabari yuboramiz (<code> bilan, tap-and-hold orqali nusxalash
            # mumkin bo'lsin deb), alertni esa faqat qisqa bildirishnoma sifatida.
            await update.callback_query.answer("⚠️ Xato yuz berdi, tafsilotlar pastda.", show_alert=False)
            if update.callback_query.message:
                await update.callback_query.message.answer(
                    f"⚠️ <b>Kutilmagan xato:</b>\n<code>{err_text}</code>", parse_mode="HTML",
                )
        elif update.message:
            await update.message.answer(f"⚠️ <b>Kutilmagan xato:</b>\n<code>{err_text}</code>", parse_mode="HTML")
    except Exception:
        pass
    return True


async def notify_bot_down(bot: Bot, bot_row, reason: str, log_tail: str = None):
    """
    Bot to'xtaganda/ishga tushmay qolganda — nafaqat egasiga, balki BARCHA
    admin va superadminlarga ham darhol xabar yuboradi (log bilan birga).

    MUHIM FIX: ilgari faqat crash_watchdog egasiga xabar yuborar edi, admin/
    superadminlar esa umuman bilmasdi. Yana ham jiddiyrog'i — platforma qayta
    ishga tushganda (Render restart/spin-down) restore_running_bots muvaffaqiyatsiz
    bo'lsa, HECH KIMGA xabar bormasdi (faqat server logiga yozilardi, uni odatda
    hech kim kuzatib turmaydi) — foydalanuvchi ertalab botni o'zi tekshirib
    ko'rmaguncha muammodan bexabar qolardi.
    """
    label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_row['bot_id']}"
    text = format_log_block(f"⚠️ {label} — {reason}", log_tail or "Log topilmadi.")
    recipients = {bot_row["owner_id"], *ADMIN_IDS, *SUPERADMIN_IDS}
    for uid in recipients:
        try:
            await bot.send_message(
                uid, text, parse_mode="HTML",
                reply_markup=crash_notify_kb(
                    bot_row["bot_id"], has_env=bool(db.list_envs(bot_row["bot_id"])),
                    viewer_is_vip=is_admin(uid),
                ),
            )
        except Exception:
            pass


async def restore_running_bots(bot: Bot):
    """
    Render Free Tier diski ephemeral bo'lgani uchun, HosterBot platformasining o'zi
    qayta ishga tushganda (spin-down yoki redeploy) diskdagi bot kod fayllari HAM,
    xotiradagi "qaysi processlar ishlayapti" ro'yxati HAM yo'qolib qoladi.
    Buning oldini olmasak, crash_watchdog bazada "running" deb yozilgan botlarni
    tekshirib, jarayon topolmagani uchun ularni soxta ravishda "crashed" deb
    belgilab, egalariga bo'lmagan xato haqida xabar yuborib yuboradi.

    Shu funksiya platforma yuqorilaganda (webhook o'rnatilishidan oldin) avval
    "running" deb belgilangan botlarni original faylidan (storage_file_id —
    Telegram'da saqlangan asl kod) qayta tiklab, qayta ishga tushiradi.
    """
    for bot_row in db.list_all_bots():
        if bot_row["status"] != "running":
            continue
        if db.is_banned(bot_row["owner_id"]):
            db.set_bot_status(bot_row["bot_id"], "stopped", None)
            continue

        bot_id = bot_row["bot_id"]
        label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
        workdir = bot_workdir(bot_id)
        start_cmd = bot_row["start_cmd"]

        # DIQQAT (bug fix): oldin faqat top-level papka tekshirilardi
        # (os.listdir). Zip orqali deploy qilingan botlarda haqiqiy kod
        # ko'pincha ichki "wrapper" papkada turadi (resolve_project_root),
        # shu sabab bu tekshiruv HAR DOIM "code_missing=True" berardi —
        # hatto kod diskda hali joyida bo'lsa ham. Natijada har restart'da
        # keraksiz qayta-yuklash bo'lardi, va agar bot.download() muvaffaqiyatsiz
        # bo'lsa, sog'lom bot bekorga "crashed" deb belgilanardi.
        # Endi butun daraxt (os.walk) bo'yicha tekshiramiz.
        code_missing = True
        if os.path.isdir(workdir):
            for _, _, files in os.walk(workdir):
                if any(f.endswith(".py") for f in files):
                    code_missing = False
                    break

        if code_missing:
            if bot_row["data_backup_file_id"]:
                # YANGI FEATURE: avval botning umumiy "disk" snapshot'ini tiklaymiz
                # (bu botning o'zi yaratgan baza/JSON/media fayllarini ham qamraydi).
                # Pastdagi qadam (storage_file_id'dan) baribir eng SO'NGGI kodni
                # buning ustidan qayta yozadi — shu bilan freshness kafolatlanadi.
                os.makedirs(workdir, exist_ok=True)
                await restore_bot_data(bot, bot_row["data_backup_file_id"], workdir)

            if not bot_row["storage_file_id"]:
                log.warning(f"{label}: kod fayli yo'qolgan va storage_file_id yo'q — tiklab bo'lmadi.")
                db.set_bot_status(bot_id, "crashed", None)
                await notify_bot_down(
                    bot, bot_row,
                    "platforma qayta ishga tushdi (Render restart), lekin bu botning kod fayli "
                    "diskda ham, zaxirada ham topilmadi — botni qayta yuklashingiz kerak.",
                )
                continue
            try:
                tmp_path = os.path.join(workdir, "_restore_download")
                await bot.download(bot_row["storage_file_id"], destination=tmp_path)
                if bot_row["is_zip"]:
                    extract_zip(tmp_path, workdir)
                    os.remove(tmp_path)
                    workdir = resolve_project_root(workdir)
                else:
                    match = re.search(r'([^\s"\']+\.py)', start_cmd)
                    py_name = os.path.basename(match.group(1)) if match else "main.py"
                    os.replace(tmp_path, os.path.join(workdir, py_name))
                normalize_requirements_filename(workdir)
                fix_all_py_encodings(workdir)
            except Exception as e:
                log.warning(f"{label}: kodni tiklashda xato: {e}")
                db.set_bot_status(bot_id, "crashed", None)
                await notify_bot_down(
                    bot, bot_row,
                    f"platforma qayta ishga tushdi, lekin kodni zaxiradan tiklashda xato: {e}",
                )
                continue
        elif bot_row["is_zip"]:
            # Kod diskda hali joyida (disk o'chmagan) — lekin zip botlarda haqiqiy
            # loyiha ichki wrapper papkada bo'lishi mumkin. workdir'ni shunga moslab
            # resolve qilmasak, keyingi build/start/log yo'llari noto'g'ri
            # (tashqi, bo'sh) papkaga ishora qilib qoladi.
            workdir = resolve_project_root(workdir)

        # MUHIM FIX: agar bu bot uchun alohida backup qilingan requirements.txt
        # bo'lsa (dastlabki deploy'da .py+requirements.txt sifatida yuklangan,
        # yoki keyinchalik "requirements.txt almashtirish" orqali yangilangan),
        # uni HAR DOIM workdir'ga qayta yozamiz — disk tozalangan bo'lsa ham
        # (code_missing=True), qolgan bo'lsa ham (eng so'nggi versiya ustun bo'lsin
        # deb). Ilgari bu umuman qilinmagan edi — shu sabab requirements.txt har
        # restart'da "o'zi o'chib ketayotganday" ko'rinardi.
        if bot_row["requirements_file_id"]:
            try:
                await bot.download(bot_row["requirements_file_id"], destination=os.path.join(workdir, "requirements.txt"))
            except Exception as e:
                log.warning(f"{label}: requirements.txt'ni tiklashda xato: {e}")

        envs = {e["key"]: e["value"] for e in db.list_envs(bot_id)}
        try:
            log_path = os.path.join(workdir, "run.log")
            with open(log_path, "a", encoding="utf-8") as log_file:
                build_ok = run_build_command(workdir, bot_row["build_cmd"] or "", log_file)
            if not build_ok:
                db.set_bot_status(bot_id, "crashed", None)
                await notify_bot_down(
                    bot, bot_row,
                    "platforma qayta ishga tushgach, botni qayta build qilishda xato yuz berdi.",
                    log_tail=read_log_tail(workdir, n_lines=30),
                )
                continue
            pid = start_bot_process(bot_id, workdir, start_cmd, envs)
            db.set_bot_status(bot_id, "running", pid)
            log.info(f"{label}: platform qayta ishga tushgach avtomatik tiklandi (pid={pid}).")
        except Exception as e:
            log.warning(f"{label}: qayta ishga tushirishda xato: {e}")
            db.set_bot_status(bot_id, "crashed", None)
            await notify_bot_down(
                bot, bot_row,
                f"platforma qayta ishga tushgach, botni ishga tushirishda xato: {e}",
                log_tail=read_log_tail(workdir, n_lines=30),
            )


async def crash_watchdog():
    """
    Render'dagi kabi: har WATCHDOG_INTERVAL_SEC soniyada "running" deb belgilangan
    botlarni tekshiradi. Agar process kutilmaganda o'lgan bo'lsa (masalan runtime
    xatosi, xotira yetishmasligi va h.k.), holatini "crashed"ga o'zgartiradi va
    egasiga HAMDA barcha admin/superadminlarga log bilan birga darhol xabar beradi.
    """
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SEC)
        try:
            for bot_row in db.list_all_bots():
                if bot_row["status"] != "running":
                    continue
                if is_running(bot_row["bot_id"]):
                    continue

                db.set_bot_status(bot_row["bot_id"], "crashed", None)
                crash_log = read_log_tail(bot_row["code_path"], n_lines=30)
                await notify_bot_down(bot, bot_row, "kutilmaganda to'xtab qoldi", log_tail=crash_log)
                await backup_database(bot)
        except Exception as e:
            log.warning(f"Watchdog xatoligi: {e}")


async def billing_watchdog():
    """
    Har BILLING_WATCHDOG_INTERVAL_SEC soniyada Stars orqali (admin tasdig'isiz)
    hostlangan, to'lov muddati (paid_until) o'tib ketgan botlarni tekshiradi.

    AVTO-UZAYTIRISH: agar foydalanuvchi balansida yana kamida bitta davr uchun
    (stars_per_unit) yetarli stars bo'lsa va u bloklanmagan bo'lsa — botni
    to'xtatmasdan, AVTOMATIK balansdan yechib, paid_until'ni yana bir davrga
    suradi (foydalanuvchi "Uzaytirish" tugmasini bosishi shart emas). Masalan
    10 kunlik balans bo'lsa, har 24 soatda birma-bir avtomatik yechiladi va
    balans tugagunicha bot ishlab turadi. Faqat balans yetarli bo'lmaganda yoki
    foydalanuvchi bloklanganda bot to'xtatiladi.
    """
    while True:
        await asyncio.sleep(BILLING_WATCHDOG_INTERVAL_SEC)
        try:
            for bot_row in db.list_expired_stars_bots():
                bot_id = bot_row["bot_id"]
                owner_id = bot_row["owner_id"]
                label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"

                stars_per_unit = db.get_stars_per_unit()
                seconds_per_unit = db.get_seconds_per_unit()
                balance = db.get_user_balance(owner_id)

                if not db.is_banned(owner_id) and balance >= stars_per_unit:
                    db.add_user_balance(
                        owner_id, -stars_per_unit,
                        reason=f"Avto-uzaytirish (#{bot_id}, +{seconds_per_unit // 3600} soat)",
                    )
                    now = int(time.time())
                    base = bot_row["paid_until"] if (bot_row["paid_until"] and bot_row["paid_until"] > now) else now
                    new_paid_until = base + seconds_per_unit
                    db.set_bot_stars_payment(bot_id, new_paid_until)
                    await backup_database(bot)
                    try:
                        await bot.send_message(
                            owner_id,
                            f"🔄 <b>{html.escape(label)}</b> uchun vaqt avtomatik uzaytirildi "
                            f"(-{stars_per_unit}⭐️). Qolgan balans: {balance - stars_per_unit}⭐️.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    continue

                stop_bot_process(bot_id)
                db.set_bot_status(bot_id, "stopped", None)
                try:
                    await bot.send_message(
                        owner_id,
                        f"⏱ <b>{html.escape(label)}</b> uchun to'langan vaqt tugadi, bot to'xtatildi.\n\n"
                        f"\"💳 Hisob\" orqali balansingizni to'ldirib, botni yana uzaytirishingiz mumkin.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                await backup_database(bot)
        except Exception as e:
            log.warning(f"Billing watchdog xatoligi: {e}")


async def auto_unblock_watchdog():
    """
    Superadmin tomonidan host qilish huquqi vaqtincha olib tashilgan (is_banned=1),
    lekin AVVAL to'lov qilgan (lifetime_topup_stars>0) foydalanuvchilar uchun
    24 soatdan keyin avtomatik ochadi. To'lov qilmagan foydalanuvchilar uchun
    blocked_until=None qo'yilgani sababli bu funksiya ularga tegmaydi.
    """
    while True:
        await asyncio.sleep(BILLING_WATCHDOG_INTERVAL_SEC)
        try:
            for user_row in db.list_users_pending_auto_unblock():
                telegram_id = user_row["telegram_id"]
                db.set_user_banned(telegram_id, False)
                db.set_user_blocked_until(telegram_id, None)
                try:
                    await bot.send_message(
                        telegram_id,
                        "🔓 24 soat o'tdi — host qilish huquqingiz avtomatik tiklandi.",
                    )
                except Exception:
                    pass
                await backup_database(bot)
        except Exception as e:
            log.warning(f"Auto-unblock watchdog xatoligi: {e}")


async def approval_expiry_watchdog():
    """
    Admin tomonidan berilgan ruxsatning majburiy muddati (approved_until) o'tib
    ketgan foydalanuvchilarni topib, ruxsatini avtomatik qaytarib oladi: status
    'pending'ga qaytadi, limit tozalanadi, ishlab turgan (Stars orqali emas,
    admin ruxsati bilan hostlangan) botlari to'xtatiladi.
    """
    while True:
        await asyncio.sleep(BILLING_WATCHDOG_INTERVAL_SEC)
        try:
            for user_row in db.list_expired_approvals():
                telegram_id = user_row["telegram_id"]
                db.set_user_status(telegram_id, "pending")
                db.set_user_max_bots(telegram_id, None)
                db.set_user_approved_until(telegram_id, None)

                stopped = []
                for b in db.list_user_bots(telegram_id):
                    if b["status"] == "running" and not b["stars_hosted"]:
                        stop_bot_process(b["bot_id"])
                        db.set_bot_status(b["bot_id"], "stopped", None)
                        stopped.append(b["bot_username"] or b["display_name"] or f"Bot #{b['bot_id']}")

                try:
                    stopped_text = f"\nTo'xtatilgan botlar: {', '.join(stopped)}" if stopped else ""
                    await bot.send_message(
                        telegram_id,
                        f"⏱ Admin tomonidan berilgan host qilish muddatingiz tugadi, "
                        f"ruxsat avtomatik qaytarib olindi.{stopped_text}\n\n"
                        f"Davom etish uchun adminga qaytadan murojaat qiling yoki \"💳 Hisob\" "
                        f"orqali Stars bilan o'zingiz to'lang.",
                    )
                except Exception:
                    pass
                await backup_database(bot)
        except Exception as e:
            log.warning(f"Approval expiry watchdog xatoligi: {e}")


async def star_balance_watchdog():
    """
    Har STAR_BALANCE_WATCHDOG_INTERVAL_SEC soniyada botning HAQIQIY Stars
    balansini (get_my_star_balance) tekshiradi. Balans STAR_BALANCE_ALERT_THRESHOLD
    (1000⭐) ga yetgan/oshgan bo'lsa va bu haqda hali xabar berilmagan bo'lsa,
    barcha superadminlarga bir marta xabar yuboriladi (Fragment orqali withdraw
    endi mumkinligi haqida). Balans qaytadan chegaradan pastga tushib, keyin
    yana oshsa — flag qayta tiklanadi va yangi xabar yuboriladi.
    """
    while True:
        await asyncio.sleep(STAR_BALANCE_WATCHDOG_INTERVAL_SEC)
        try:
            star_amount = await bot.get_my_star_balance()
            amount = star_amount.amount
            already_alerted = db.get_setting("star_balance_alert_sent", "0") == "1"

            if amount >= STAR_BALANCE_ALERT_THRESHOLD and not already_alerted:
                db.set_setting("star_balance_alert_sent", "1")
                for admin_id in SUPERADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            f"⭐️ <b>Bot Stars balansi {amount} ga yetdi!</b>\n\n"
                            f"Fragment orqali withdraw qilish uchun minimal chegara "
                            f"({STAR_BALANCE_ALERT_THRESHOLD}⭐️) bajarildi.\n\n"
                            f"Eslatma: har bir Star kelgan kunidan 21 kun o'tishi kerak, "
                            f"shundan keyingina withdraw qilinadi. fragment.com'ga botni "
                            f"yaratgan akkaunt bilan kirib, TON wallet ulab yeching.",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            elif amount < STAR_BALANCE_ALERT_THRESHOLD and already_alerted:
                # Balans pasayib ketdi (masalan gift/uzatish orqali) — flag'ni
                # tozalaymiz, shunda keyingi safar chegaraga yetganda yana xabar beriladi.
                db.set_setting("star_balance_alert_sent", "0")
        except Exception as e:
            log.warning(f"Star balance watchdog xatoligi: {e}")


async def data_backup_watchdog():
    """
    YANGI (o'zgarish-asosli): endi belgilangan interval bilan EMAS, balki
    HAR BOT UCHUN workdir'da haqiqiy o'zgarish bo'lgandagina backup qilinadi —
    "hech narsa o'zgarmagan bo'lsa, hech qanday backup ham yo'q" tamoyili bilan.

    Ishlash tamoyili: har DATA_CHANGE_CHECK_INTERVAL_SEC (1) soniyada workdir'ning
    YENGIL imzosi (fayl soni/hajmi/vaqti — kontentni o'qimasdan) tekshiriladi.
    O'zgarish birinchi marta sezilgan ONDA "muddat" DATA_CHANGE_DEBOUNCE_SEC (2)
    soniyaga belgilanadi — shu 2 soniya ichida yana necha marta o'zgarish kelsa
    ham, muddat SURILMAYDI (bitta backup barchasini birga oladi). Muddat kelganda,
    O'SHA PAYTDAGI holat backup qilinadi. Agar backup davomida (upload paytida)
    yana yozish bo'lib qolsa, u holat diskda/RAM'da qoladi va KEYINGI tekshiruv
    siklida yangi o'zgarish sifatida avtomatik tutib olinadi — hech narsa
    yo'qolmaydi, faqat navbatdagi backup'ga kiradi.
    """
    last_sig: dict[int, tuple] = {}
    due_at: dict[int, float] = {}
    while True:
        await asyncio.sleep(DATA_CHANGE_CHECK_INTERVAL_SEC)
        now = time.time()
        for bot_row in db.list_all_bots():
            if bot_row["status"] != "running":
                continue
            bot_id = bot_row["bot_id"]
            workdir = bot_row["code_path"] or bot_workdir(bot_id)
            sig = _workdir_signature(workdir)

            if last_sig.get(bot_id) != sig:
                last_sig[bot_id] = sig
                due_at.setdefault(bot_id, now + DATA_CHANGE_DEBOUNCE_SEC)
                continue

            if bot_id in due_at and now >= due_at.pop(bot_id):
                try:
                    file_id, err = await backup_bot_data(bot, bot_row, workdir)
                    if file_id:
                        db.set_data_backup_file_id(bot_id, file_id)
                    elif err:
                        log.info(f"Bot #{bot_id}: data backup o'tkazib yuborildi: {err}")
                except Exception as e:
                    log.warning(f"Bot #{bot_id}: data backup watchdog xatoligi: {e}")


async def on_startup(app: web.Application):
    await restore_database(bot)
    db.init_db()
    await restore_running_bots(bot)
    asyncio.create_task(crash_watchdog())
    asyncio.create_task(billing_watchdog())
    asyncio.create_task(auto_unblock_watchdog())
    asyncio.create_task(approval_expiry_watchdog())
    asyncio.create_task(star_balance_watchdog())
    asyncio.create_task(data_backup_watchdog())

    if not WEBHOOK_BASE_URL:
        log.warning(
            "WEBHOOK_BASE_URL / RENDER_EXTERNAL_URL topilmadi — webhook o'rnatilmadi. "
            "Render'da bu avtomatik keladi; boshqa joyda bo'lsa WEBHOOK_BASE_URL ni qo'ling."
        )
        return
    webhook_url = WEBHOOK_BASE_URL.rstrip("/") + WEBHOOK_PATH
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    log.info(f"Webhook o'rnatildi: {webhook_url}")


async def on_shutdown(app: web.Application):
    # MUHIM FIX: Render odatda process'ni o'chirishdan oldin SIGTERM yuborib,
    # bir necha soniya "muhlat" beradi (keyin SIGKILL). Shu muhlatdan foydalanib,
    # barcha ishlab turgan botlarning DATA BACKUP'ini oxirgi marta olib
    # ulguramiz — bu davriy (DATA_BACKUP_INTERVAL_SEC) intervalning qoldirgan
    # "yo'qotish oynasi"ni REJALASHTIRILGAN har qanday restart/redeploy/spin-down
    # uchun deyarli nolga tushiradi. Faqat process SIGNALSIZ, kutilmagan tarzda
    # o'lib qolsa (masalan qattiq OOM-kill) — bu himoya ishlamaydi, davriy
    # backup esa o'shanday holatlar uchun ikkinchi qatlam bo'lib qoladi.
    try:
        running_bots = [r for r in db.list_all_bots() if r["status"] == "running"]
        if running_bots:
            log.info(f"Shutdown: {len(running_bots)} ta ishlab turgan bot uchun so'nggi data backup olinmoqda...")

            async def _final_backup(bot_row):
                workdir = bot_row["code_path"] or bot_workdir(bot_row["bot_id"])
                try:
                    file_id, err = await backup_bot_data(bot, bot_row, workdir)
                    if file_id:
                        db.set_data_backup_file_id(bot_row["bot_id"], file_id)
                except Exception as e:
                    log.warning(f"Bot #{bot_row['bot_id']}: shutdown backup xatoligi: {e}")

            await asyncio.wait_for(
                asyncio.gather(*(_final_backup(r) for r in running_bots), return_exceptions=True),
                timeout=20,
            )
    except asyncio.TimeoutError:
        log.warning("Shutdown backup: 20s muhlat tugadi, ulgurgan botlarniki saqlandi.")
    except Exception as e:
        log.warning(f"Shutdown backup umumiy xatoligi: {e}")

    # DIQQAT: bu yerda bot.delete_webhook() ATAYLAB chaqirilmaydi.
    # Render service'ni istalgan sababdan (redeploy, ichki restart, spin-down) qayta
    # ishga tushirishi mumkin — agar shutdown paytida webhook o'chirilsa-yu, yangi
    # instance sekinroq ko'tarilsa, orada webhook "bo'sh" holatda qolib ketishi mumkin.
    # on_startup har doim set_webhook'ni qayta chaqiradi, shuning uchun webhook'ni
    # eskirtirib qo'yishning hojati yo'q.
    await bot.session.close()


async def health_check(request: web.Request):
    # UptimeRobot shu endpointga ping qiladi, bot uxlab qolmasligi uchun
    return web.Response(text="HosterBot ishlayapti ✅")


class _WebhookNotifyMessage:
    """_rebuild_and_start (handlers/bot_actions.py) 'Message' obyekti kutadi
    (aiogram Message'ning .answer() metodi orqali javob yuboradi) — lekin
    GitHub webhook so'rovi HTTP kontekstida keladi, hech qanday Telegram
    Message yo'q. Shu sabab minimal 'soxta Message': faqat .answer() ni
    bot.send_message(owner_id, ...) ga proksi qiladi, botning o'z egasiga
    yozadi."""
    def __init__(self, bot: Bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def answer(self, text: str, parse_mode: str = None, **kwargs):
        try:
            await self._bot.send_message(self._chat_id, text, parse_mode=parse_mode)
        except Exception:
            pass  # foydalanuvchi botni bloklagan bo'lishi mumkin — webhook javobini bloklamaymiz


async def github_webhook_handler(request: web.Request):
    """GitHub push webhook qabul qiladi: /gh-webhook/{bot_id}/{secret}.
    Secret noto'g'ri bo'lsa yoki bot topilmasa 404 qaytaradi (bot mavjudligi
    haqida ma'lumot sizib chiqmasligi uchun — 403 emas, 404)."""
    try:
        bot_id = int(request.match_info["bot_id"])
    except (KeyError, ValueError):
        return web.Response(status=404, text="not found")
    secret = request.match_info.get("secret", "")

    bot_row = db.get_bot_by_webhook(bot_id, secret)
    if bot_row is None:
        return web.Response(status=404, text="not found")

    # GitHub Ping event'ini ham qabul qilamiz (webhook birinchi qo'shilganda
    # GitHub avtomatik test so'rov yuboradi) — bu holda hech narsa deploy
    # qilmasdan, faqat 200 qaytaramiz (aks holda GitHub webhook'ni "muvaffaqiyatsiz"
    # deb belgilab qo'yishi mumkin).
    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type == "ping":
        return web.Response(status=200, text="pong")
    if event_type != "push":
        return web.Response(status=200, text="ignored (not a push event)")

    if not bot_row.get("github_url"):
        return web.Response(status=400, text="bot is not linked to a GitHub repo")

    from services.github_deploy import parse_github_url

    try:
        owner, repo = parse_github_url(bot_row["github_url"])
    except GitHubDeployError:
        return web.Response(status=500, text="invalid stored github_url")

    branch = bot_row.get("github_branch") or "main"
    tmp_dir = f"/tmp/gh_webhook_{bot_id}"
    os.makedirs(tmp_dir, exist_ok=True)
    zip_path = os.path.join(tmp_dir, f"{repo}.zip")

    fake_message = _WebhookNotifyMessage(bot, bot_row["owner_id"])
    bot_label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"

    try:
        await download_repo_zip(owner, repo, zip_path, branch=branch)
    except GitHubDeployError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await fake_message.answer(f"⚠️ {bot_label} — GitHub push kelgan, lekin qayta yuklab bo'lmadi: {e}")
        return web.Response(status=200, text="download failed, owner notified")
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.exception(f"GitHub webhook: repo yuklashda xato (bot_id={bot_id})")
        return web.Response(status=200, text="download failed")

    try:
        # Eski kodni yangi bilan almashtiramiz: avval eski papkani tozalab,
        # keyin yangi ZIP'ni o'sha yerga extract qilamiz (bot_actions.py'dagi
        # fix_code bilan bir xil xavfsizlik tamoyili — lekin bu yerda butun
        # papka, bitta fayl emas, shu sabab papka darajasida almashtiramiz).
        old_code_path = bot_row["code_path"]
        extract_dir = os.path.join(tmp_dir, "extracted")
        extract_zip(zip_path, extract_dir)
        project_root = resolve_project_root(extract_dir)

        if os.path.isdir(old_code_path):
            shutil.rmtree(old_code_path, ignore_errors=True)
        shutil.copytree(project_root, old_code_path)
        normalize_requirements_filename(old_code_path)
        fix_all_py_encodings(old_code_path)
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.exception(f"GitHub webhook: kodni joylashtirishda xato (bot_id={bot_id})")
        await fake_message.answer(f"⚠️ {bot_label} — GitHub push kelgan, lekin kodni joylashtirishda xato: {e}")
        return web.Response(status=200, text="deploy failed, owner notified")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    await fake_message.answer(f"🐙 {bot_label} — GitHub push qabul qilindi, qayta build va ishga tushirilmoqda...")
    await _rebuild_and_start(bot_id, bot, fake_message)

    return web.Response(status=200, text="ok")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_post("/gh-webhook/{bot_id}/{secret}", github_webhook_handler)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
