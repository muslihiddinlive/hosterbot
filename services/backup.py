"""
services/backup.py

Render Free Tier diski ephemeral bo'lgani uchun (redeploy/restart'da o'chadi),
platform.db faylini STORAGE_GROUP_ID guruhiga backup qilamiz va kerak bo'lganda
qayta tiklaymiz.

Usul: backup faylini guruhga document sifatida yuboramiz va PIN qilamiz.
Telegram getChat() har doim guruhdagi "eng so'nggi pin qilingan xabar"ni qaytaradi,
shuning uchun eski pinlarni o'chirishning hojati yo'q — shunchaki eng oxirgi
backup avtomatik "joriy" hisoblanadi.
"""
import logging

from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramMigrateToChat

from config import STORAGE_GROUP_ID, DB_PATH

log = logging.getLogger("hosterbot.backup")


async def backup_database(bot: Bot) -> tuple[bool, str | None]:
    """Qaytaradi: (muvaffaqiyatli_bo'ldimi, xato_matni_yoki_None).
    Chaqiruvchi xohlasa xatoni foydalanuvchiga/adminga ko'rsatishi mumkin — avval bu
    funksiya xatoni yutib yuborardi, endi chaqiruvchiga qaror qabul qilish imkonini beradi."""
    try:
        sent = await bot.send_document(
            STORAGE_GROUP_ID,
            FSInputFile(DB_PATH, filename="platform.db"),
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
    """Agar guruhda pin qilingan backup bo'lsa, uni yuklab platform.db o'rniga qo'yadi."""
    try:
        chat = await bot.get_chat(STORAGE_GROUP_ID)
        pinned = chat.pinned_message
        if pinned is None or pinned.document is None:
            log.info("Pin qilingan DB backup topilmadi — bo'sh bazadan boshlanadi.")
            return False
        await bot.download(pinned.document, destination=DB_PATH)
        log.info("platform.db backup'dan muvaffaqiyatli tiklandi.")
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
