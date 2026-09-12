"""
services/data_backup.py

YANGI FEATURE: har bir hostlangan bot uchun ALOHIDA "disk" — Render Free Tier
diski ephemeral bo'lgani uchun (har restart'da o'chadi), botning butun ishchi
papkasi (workdir) davriy ravishda zip qilinib STORAGE_GROUP_ID guruhiga backup
qilinadi. Bu botning o'zi runtime'da yaratadigan har qanday faylni ham qamrab
oladi — SQLite baza, JSON/txt saqlash fayllari, yuklab olingan medialar va h.k.
Ilgari faqat kod (.py/.zip) va requirements.txt alohida backup qilinardi;
botning O'Z yaratgan ma'lumotlari esa har restart'da butunlay yo'qolardi.

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

from config import STORAGE_GROUP_ID

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


async def backup_bot_data(bot: Bot, bot_id: int, workdir: str) -> tuple[str | None, str | None]:
    """
    Botning butun workdir'ini zip qilib STORAGE_GROUP_ID'ga yuboradi.
    Qaytaradi: (yangi_file_id_yoki_None, xato_matni_yoki_None).
    """
    if not os.path.isdir(workdir):
        return None, "workdir topilmadi"

    zip_path = f"/tmp/hosterbot_data_{bot_id}.zip"
    try:
        size = _zip_workdir(workdir, zip_path)
        if size > MAX_BACKUP_BYTES:
            return None, f"backup {size // (1024*1024)}MB — {MAX_BACKUP_BYTES // (1024*1024)}MB limitidan katta, o'tkazib yuborildi"

        sent = await bot.send_document(
            STORAGE_GROUP_ID,
            FSInputFile(zip_path, filename=f"bot_{bot_id}_data.zip"),
            caption=f"💾 Bot #{bot_id} — data snapshot",
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
    """Berilgan file_id'dagi zip'ni yuklab, workdir'ga chiqaradi (mavjud fayllar ustidan yoziladi)."""
    zip_path = f"/tmp/hosterbot_data_restore_{os.path.basename(workdir)}.zip"
    try:
        await bot.download(file_id, destination=zip_path)
        os.makedirs(workdir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(workdir)
        return True
    except Exception as e:
        log.warning(f"Data backup'ni tiklashda xato: {e}")
        return False
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
