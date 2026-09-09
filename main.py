import asyncio
import html
import logging
import os
import re

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import ErrorEvent
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import BOT_TOKEN, WEBHOOK_BASE_URL, WEBHOOK_PATH, PORT, SUPERADMIN_IDS
import database as db
from services.backup import restore_database, backup_database
from services.deploy_manager import is_running, read_log_tail, run_build_command, start_bot_process, stop_bot_process, format_log_block
from services.file_utils import (
    bot_workdir, extract_zip, resolve_project_root,
    normalize_requirements_filename, fix_all_py_encodings,
)
from keyboards import crash_notify_kb

from handlers import start, admin_review, user_menu, add_bot, bot_actions, admin_panel, stars, help_faq, ai_chat

WATCHDOG_INTERVAL_SEC = 60
BILLING_WATCHDOG_INTERVAL_SEC = 30
STAR_BALANCE_WATCHDOG_INTERVAL_SEC = 300
STAR_BALANCE_ALERT_THRESHOLD = 1000

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
            if not bot_row["storage_file_id"]:
                log.warning(f"{label}: kod fayli yo'qolgan va storage_file_id yo'q — tiklab bo'lmadi.")
                db.set_bot_status(bot_id, "crashed", None)
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
                continue
        elif bot_row["is_zip"]:
            # Kod diskda hali joyida (disk o'chmagan) — lekin zip botlarda haqiqiy
            # loyiha ichki wrapper papkada bo'lishi mumkin. workdir'ni shunga moslab
            # resolve qilmasak, keyingi build/start/log yo'llari noto'g'ri
            # (tashqi, bo'sh) papkaga ishora qilib qoladi.
            workdir = resolve_project_root(workdir)

        envs = {e["key"]: e["value"] for e in db.list_envs(bot_id)}
        try:
            log_path = os.path.join(workdir, "run.log")
            with open(log_path, "a", encoding="utf-8") as log_file:
                build_ok = run_build_command(workdir, bot_row["build_cmd"] or "", log_file)
            if not build_ok:
                db.set_bot_status(bot_id, "crashed", None)
                continue
            pid = start_bot_process(bot_id, workdir, start_cmd, envs)
            db.set_bot_status(bot_id, "running", pid)
            log.info(f"{label}: platform qayta ishga tushgach avtomatik tiklandi (pid={pid}).")
        except Exception as e:
            log.warning(f"{label}: qayta ishga tushirishda xato: {e}")
            db.set_bot_status(bot_id, "crashed", None)


async def crash_watchdog():
    """
    Render'dagi kabi: har WATCHDOG_INTERVAL_SEC soniyada "running" deb belgilangan
    botlarni tekshiradi. Agar process kutilmaganda o'lgan bo'lsa (masalan runtime
    xatosi, xotira yetishmasligi va h.k.), holatini "crashed"ga o'zgartiradi va
    egasiga log bilan birga xabar beradi.
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
                username = bot_row["bot_username"]
                label = f"@{username}" if username else (bot_row["display_name"] or f"Bot #{bot_row['bot_id']}")
                crash_log = read_log_tail(bot_row["code_path"], n_lines=30)
                try:
                    await bot.send_message(
                        bot_row["owner_id"],
                        format_log_block(f"⚠️ {label} kutilmaganda to'xtab qoldi", crash_log),
                        parse_mode="HTML",
                        reply_markup=crash_notify_kb(bot_row["bot_id"], has_env=bool(db.list_envs(bot_row["bot_id"]))),
                    )
                except Exception:
                    pass
                await backup_database(bot)
        except Exception as e:
            log.warning(f"Watchdog xatoligi: {e}")


async def billing_watchdog():
    """
    Har BILLING_WATCHDOG_INTERVAL_SEC soniyada Stars orqali (admin tasdig'isiz)
    hostlangan, lekin to'lov muddati (paid_until) o'tib ketgan botlarni to'xtatadi
    va egasiga balansni to'ldirishni taklif qiladi.
    """
    while True:
        await asyncio.sleep(BILLING_WATCHDOG_INTERVAL_SEC)
        try:
            for bot_row in db.list_expired_stars_bots():
                bot_id = bot_row["bot_id"]
                label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
                stop_bot_process(bot_id)
                db.set_bot_status(bot_id, "stopped", None)
                try:
                    await bot.send_message(
                        bot_row["owner_id"],
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


async def on_startup(app: web.Application):
    await restore_database(bot)
    db.init_db()
    await restore_running_bots(bot)
    asyncio.create_task(crash_watchdog())
    asyncio.create_task(billing_watchdog())
    asyncio.create_task(auto_unblock_watchdog())
    asyncio.create_task(approval_expiry_watchdog())
    asyncio.create_task(star_balance_watchdog())

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


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health_check)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
