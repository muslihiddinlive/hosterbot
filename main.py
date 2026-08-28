import asyncio
import html
import logging
import os
import re

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import BOT_TOKEN, WEBHOOK_BASE_URL, WEBHOOK_PATH, PORT
import database as db
from services.backup import restore_database, backup_database
from services.deploy_manager import is_running, read_log_tail, run_build_command, start_bot_process, stop_bot_process
from services.file_utils import (
    bot_workdir, extract_zip, resolve_project_root,
    normalize_requirements_filename, fix_all_py_encodings,
)
from keyboards import crash_notify_kb

from handlers import start, admin_review, user_menu, add_bot, bot_actions, admin_panel, stars

WATCHDOG_INTERVAL_SEC = 60
BILLING_WATCHDOG_INTERVAL_SEC = 30

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
                label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_row['bot_id']}"
                crash_log = read_log_tail(bot_row["code_path"], n_lines=30)
                try:
                    await bot.send_message(
                        bot_row["owner_id"],
                        f"⚠️ <b>{html.escape(label)}</b> kutilmaganda to'xtab qoldi.\n\n"
                        f"<b>So'nggi loglar:</b>\n<pre>{html.escape(crash_log[-2500:])}</pre>",
                        parse_mode="HTML",
                        reply_markup=crash_notify_kb(bot_row["bot_id"]),
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


async def on_startup(app: web.Application):
    await restore_database(bot)
    db.init_db()
    await restore_running_bots(bot)
    asyncio.create_task(crash_watchdog())
    asyncio.create_task(billing_watchdog())
    asyncio.create_task(auto_unblock_watchdog())

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
