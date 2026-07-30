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
from services.deploy_manager import is_running, read_log_tail, run_build_command, start_bot_process
from services.file_utils import (
    bot_workdir, extract_zip, resolve_project_root,
    normalize_requirements_filename, fix_all_py_encodings,
)

from handlers import start, admin_review, user_menu, add_bot, bot_actions, admin_panel

WATCHDOG_INTERVAL_SEC = 60

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

        bot_id = bot_row["bot_id"]
        label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
        workdir = bot_workdir(bot_id)
        start_cmd = bot_row["start_cmd"]

        code_missing = not any(
            f.endswith(".py") for f in os.listdir(workdir)
        ) if os.path.isdir(workdir) else True

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
                        f"<b>So'nggi loglar:</b>\n<pre>{html.escape(crash_log[-2500:])}</pre>\n\n"
                        f"\"Mening botlarim\" bo'limidan qayta ishga tushirishingiz mumkin.",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
                await backup_database(bot)
        except Exception as e:
            log.warning(f"Watchdog xatoligi: {e}")


async def on_startup(app: web.Application):
    await restore_database(bot)
    db.init_db()
    await restore_running_bots(bot)
    asyncio.create_task(crash_watchdog())

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
