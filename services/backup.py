"""
services/backup.py

MUHIM ARXITEKTURA (RAM + Telegram): platform.db endi DISKDA UMUMAN YASHAMAYDI —
faqat xotirada (database.py'dagi ":memory:" ulanishi). Bu modul xotiradagi
bazaning to'liq baytli nusxasini (database.serialize_memory_db()) to'g'ridan-to'g'ri
Telegram'ga yuboradi (diskka vaqtincha ham yozmasdan — BufferedInputFile xotiradan
o'qiydi), va tiklashda ham baytlarni to'g'ridan-to'g'ri xotiraga yuklaydi
(database.replace_memory_db()). Bu bilan "disk to'lib qolish" muammosi ushbu
qatlam uchun butunlay yo'qoladi.

Usul: backup faylini guruhga document sifatida yuboramiz va PIN qilamiz.
Telegram getChat() har doim guruhdagi "eng so'nggi pin qilingan xabar"ni qaytaradi,
shuning uchun eski pinlarni o'chirishning hojati yo'q — shunchaki eng oxirgi
backup avtomatik "joriy" hisoblanadi.
"""
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramMigrateToChat

from config import STORAGE_GROUP_ID
import database as db

log = logging.getLogger("hosterbot.backup")


async def backup_database(bot: Bot) -> tuple[bool, str | None]:
    """Qaytaradi: (muvaffaqiyatli_bo'ldimi, xato_matni_yoki_None).
    Chaqiruvchi xohlasa xatoni foydalanuvchiga/adminga ko'rsatishi mumkin — avval bu
    funksiya xatoni yutib yuborardi, endi chaqiruvchiga qaror qabul qilish imkonini beradi."""
    try:
        raw_bytes = db.serialize_memory_db()
        sent = await bot.send_document(
            STORAGE_GROUP_ID,
            BufferedInputFile(raw_bytes, filename="platform.db"),
            caption="🗄 platform.db backup",
            disable_notification=True,
        )
        await bot.pin_chat_message(STORAGE_GROUP_ID, sent.message_id, disable_notification=True)
        return True, None
    except TelegramMigrateToChat as e:
        # Guruh oddiy guruhdan "supergroup"ga aylantirilganda Telegram ESKI ID bilan
        # ishlashni to'xtatadi va YANGI ID'ni shu xatoning o'zida qaytaradi. Bu ENV
        # sozlamasi (STORAGE_GROUP_ID) eskirib qolgani, kod xatosi emas — shuning uchun
        # aniq qaysi qiymatga o'zgartirish kerakligini to'g'ridan-to'g'ri ko'rsatamiz.
        new_id = e.migrate_to_chat_id
        msg = (
            f"Guruh supergroup'ga aylantirilgan. STORAGE_GROUP_ID ni Render Environment'da "
            f"{new_id} ga o'zgartiring va qayta deploy qiling."
        )
        log.warning(f"DB backup: guruh migratsiya bo'lgan, yangi id={new_id}")
        return False, msg
    except Exception as e:
        # Asosiy oqim (deploy, status o'zgarishi) bu xato tufayli to'xtab qolmasligi kerak —
        # shuning uchun bu yerda hali ham exception qayta ko'tarilmaydi, lekin endi
        # chaqiruvchi xato matnini oladi va xohlasa foydalanuvchiga ko'rsatadi.
        log.warning(f"DB backup muvaffaqiyatsiz: {e}")
        return False, str(e)


async def restore_database(bot: Bot) -> bool:
    """Agar guruhda pin qilingan backup bo'lsa, uni yuklab XOTIRADAGI bazaga
    (diskka emas) qo'yadi."""
    try:
        chat = await bot.get_chat(STORAGE_GROUP_ID)
        pinned = chat.pinned_message
        if pinned is None or pinned.document is None:
            log.info("Pin qilingan DB backup topilmadi — bo'sh bazadan boshlanadi.")
            return False
        file_io = await bot.download(pinned.document)
        raw_bytes = file_io.read()
        db.replace_memory_db(raw_bytes)
        log.info("platform.db backup'dan xotiraga muvaffaqiyatli tiklandi.")
        return True
    except TelegramMigrateToChat as e:
        log.warning(
            f"DB restore: STORAGE_GROUP_ID eskirgan — guruh supergroup'ga aylantirilgan. "
            f"Render Environment'da STORAGE_GROUP_ID ni {e.migrate_to_chat_id} ga o'zgartiring."
        )
        return False
    except Exception as e:
        log.warning(f"DB restore muvaffaqiyatsiz: {e}")
        return False
