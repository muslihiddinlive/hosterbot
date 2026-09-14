"""
services/data_backup.py

YANGI FEATURE: har bir hostlangan bot uchun ALOHIDA "disk" — Render Free Tier
diski ephemeral bo'lgani uchun (har restart'da o'chadi), botning butun ishchi
papkasi (workdir) davriy ravishda zip qilinib DATA STORAGE supergruruhiga
backup qilinadi. Bu botning o'zi runtime'da yaratadigan har qanday faylni ham
qamrab oladi — SQLite baza, JSON/txt saqlash fayllari, yuklab olingan
medialar va h.k. Ilgari faqat kod (.py/.zip) va requirements.txt alohida
backup qilinardi; botning O'Z yaratgan ma'lumotlari esa har restart'da
butunlay yo'qolardi.

TOPICS (forum mavzular): agar Data Storage guruhi Telegram'da "Topics"
rejimida bo'lsa (superadmin buni guruh sozlamalaridan qo'lda yoqadi va botni
"Manage Topics" huquqi bilan admin qiladi), har bot uchun ALOHIDA mavzu
(topic) avtomatik ochiladi — shu botning barcha backup'lari faqat o'sha
mavzu ichiga tushadi, guruh ichida tartibli va qo'lda ham topish oson bo'ladi.
Agar guruh forum rejimida bo'lmasa, backup'lar shunchaki guruhning umumiy
oqimiga (topic'siz) tushaveradi — funksionallik buzilmaydi, faqat tartiblanmagan
bo'ladi.

Platforma qayta ishga tushganda (restore_running_bots), agar bot uchun bunday
backup mavjud bo'lsa — avval shu umumiy snapshot workdir'ga tiklanadi, so'ng
(har doimgidek) ENG SO'NGGI kod va requirements.txt backup'lari uning ustidan
qayta yoziladi — shunda kod har doim eng yangi versiyada bo'lishi kafolatlanadi,
snapshot esa faqat "qolgan" (foydalanuvchi ma'lumotlari) fayllarni ta'minlaydi.
"""
import logging
import os
import zipfile

from aiogram import Bot
from aiogram.types import FSInputFile

import database as db
from services.file_utils import extract_zip

log = logging.getLogger("hosterbot.data_backup")

# MUHIM: Telegram Bot API'da ikkita XILMA-XIL chegara bor —
# - YUKLASH (bot document yuborishi): 50MB gacha.
# - YUKLAB OLISH (bot faylni qaytarib download qilishi): FAQAT 20MB gacha!
# Backup faylni platforma qayta ishga tushganda albatta QAYTARIB YUKLAB OLISH
# (restore) kerak bo'lgani uchun, HAQIQIY chegara — 20MB, 50MB emas. Xavfsizlik
# zaxirasi bilan 18MB'da to'xtatamiz.
MAX_BACKUP_BYTES = 18 * 1024 * 1024

# Zip'ga kiritilmaydigan, keraksiz/vaqtinchalik papkalar va fayllar.
_EXCLUDED_DIR_NAMES = {"__pycache__", ".git", ".venv", "venv", "node_modules"}
_EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo")


def _zip_workdir(workdir: str, zip_path: str) -> int:
    """workdir'ni zip_path'ga zip qiladi (keraksiz fayllarsiz). Qaytaradi: yakuniy hajmi (bayt)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIR_NAMES]
            for fname in files:
                if fname.endswith(_EXCLUDED_FILE_SUFFIXES):
                    continue
                full_path = os.path.join(root, fname)
                if full_path == zip_path:
                    continue
                arcname = os.path.relpath(full_path, workdir)
                try:
                    zf.write(full_path, arcname)
                except OSError:
                    continue  # o'qib bo'lmaydigan/yo'qolib qolgan fayl — o'tkazib yuboramiz
    return os.path.getsize(zip_path)


async def _ensure_topic(bot: Bot, group_id: int, bot_row) -> int | None:
    """Bot uchun mavjud topic bo'lsa shuni qaytaradi; bo'lmasa yaratishga urinadi.
    Guruh forum rejimida bo'lmasa (yoki botda huquq yo'q bo'lsa), jimgina None
    qaytaradi — bu holda backup guruhning umumiy oqimiga tushadi."""
    if bot_row["data_topic_id"]:
        return bot_row["data_topic_id"]
    try:
        bot_id = bot_row["bot_id"]
        label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
        topic = await bot.create_forum_topic(group_id, name=f"#{bot_id} — {label}"[:128])
        db.set_data_topic_id(bot_id, topic.message_thread_id)
        return topic.message_thread_id
    except Exception as e:
        log.info(f"Bot #{bot_row['bot_id']}: topic yaratib bo'lmadi (guruh forum rejimida emas yoki huquq yo'q): {e}")
        return None


def _workdir_signature(workdir: str) -> tuple:
    """Yengil 'imzo' — fayllarni o'qimasdan, faqat soni/umumiy hajmi/eng so'nggi
    o'zgartirilgan vaqti orqali workdir o'zgarganmi-yo'qmi tekshirish uchun.
    Bu har 1 soniyada ko'p bot uchun ham chaqirilaveradigan bo'lgani uchun
    ATAYLAB tez — hech qanday fayl kontenti o'qilmaydi, faqat os.stat()."""
    total_size = 0
    max_mtime = 0.0
    count = 0
    try:
        for root, dirs, files in os.walk(workdir):
            dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIR_NAMES]
            for fname in files:
                if fname.endswith(_EXCLUDED_FILE_SUFFIXES):
                    continue
                try:
                    st = os.stat(os.path.join(root, fname))
                except OSError:
                    continue
                total_size += st.st_size
                if st.st_mtime > max_mtime:
                    max_mtime = st.st_mtime
                count += 1
    except OSError:
        pass
    return (count, total_size, max_mtime)


async def backup_bot_data(bot: Bot, bot_row, workdir: str) -> tuple[str | None, str | None]:
    """
    Botning butun workdir'ini zip qilib Data Storage guruhiga (bor bo'lsa —
    shu botning o'z topic'iga) yuboradi.
    Qaytaradi: (yangi_file_id_yoki_None, xato_matni_yoki_None).
    """
    bot_id = bot_row["bot_id"]
    if not os.path.isdir(workdir):
        return None, "workdir topilmadi"

    group_id = db.get_data_storage_group_id()
    topic_id = await _ensure_topic(bot, group_id, bot_row)

    zip_path = f"/tmp/hosterbot_data_{bot_id}.zip"
    try:
        size = _zip_workdir(workdir, zip_path)
        if size > MAX_BACKUP_BYTES:
            return None, f"backup {size // (1024*1024)}MB — {MAX_BACKUP_BYTES // (1024*1024)}MB limitidan katta, o'tkazib yuborildi"

        sent = await bot.send_document(
            group_id,
            FSInputFile(zip_path, filename=f"bot_{bot_id}_data.zip"),
            caption=f"💾 Bot #{bot_id} — data snapshot",
            message_thread_id=topic_id,
            disable_notification=True,
        )
        return sent.document.file_id, None
    except Exception as e:
        log.warning(f"Bot #{bot_id}: data backup xatoligi: {e}")
        return None, str(e)
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass


async def restore_bot_data(bot: Bot, file_id: str, workdir: str) -> bool:
    """Berilgan file_id'dagi zip'ni yuklab, workdir'ga chiqaradi (mavjud fayllar ustidan yoziladi).
    Topic qaysi guruhda bo'lishidan qat'i nazar ishlaydi — file_id o'zi yetarli."""
    zip_path = f"/tmp/hosterbot_data_restore_{os.path.basename(workdir)}.zip"
    try:
        await bot.download(file_id, destination=zip_path)
        # extract_zip (file_utils) zip-slip himoyasi bilan chiqaradi — bu backup
        # odatda platformaning o'zi yaratgan bo'lsa-da, chuqurlikni buzuvchi
        # mudofaa (defense-in-depth) sifatida shu yo'l bilan qat'iy tekshiramiz.
        extract_zip(zip_path, workdir)
        return True
    except Exception as e:
        log.warning(f"Data backup'ni tiklashda xato: {e}")
        return False
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
