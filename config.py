"""
config.py
Barcha sozlamalar ENV orqali olinadi (Render'da Environment tab'ga qo'yiladi).

Kerakli ENV o'zgaruvchilari:
  BOT_TOKEN        - manager (asosiy) botning tokeni
  ADMIN_IDS        - vergul bilan ajratilgan admin telegram_id lar (masalan: 111,222)
  SUPERADMIN_IDS   - vergul bilan ajratilgan superadmin telegram_id lar
  STORAGE_GROUP_ID - kod/env fayllari yuboriladigan Telegram guruh ID si (masalan: -1001234567890)
                      Bu guruh "ma'lumotlar bazasi" vazifasini o'taydi — Render diski
                      redeploy'da tozalanadi, shuning uchun kodlar/zip'lar shu guruhga
                      yuborilib, file_id orqali istalgan vaqt qayta yuklab olinadi.
  MAX_BOTS_PER_USER - (ixtiyoriy, default=3) bitta foydalanuvchi deploy qila oladigan bot soni
"""
import os

BOT_TOKEN = os.environ["BOT_TOKEN"]

ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
SUPERADMIN_IDS = {int(x) for x in os.environ.get("SUPERADMIN_IDS", "").split(",") if x.strip()}

STORAGE_GROUP_ID = int(os.environ["STORAGE_GROUP_ID"])

MAX_BOTS_PER_USER = int(os.environ.get("MAX_BOTS_PER_USER", "3"))

# ---- Webhook (Render Free Tier Web Service uchun) ----
# Render web service'lar avtomatik RENDER_EXTERNAL_URL beradi (masalan https://hosterbot.onrender.com).
# Agar boshqa joyda host qilsangiz, WEBHOOK_BASE_URL ni qo'lda kiriting.
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
PORT = int(os.environ.get("PORT", "10000"))

# Har bir deploy qilingan bot uchun resurs chegarasi (subprocess RLIMIT orqali).
# MUHIM TUSHUNCHA: RLIMIT_AS haqiqiy ishlatilayotgan RAM emas, balki jarayon so'ragan
# VIRTUAL manzil maydonini cheklaydi. opencv (ayniqsa ffmpeg kodeklari bilan birga
# keladigan "headless" versiyasi) va shunga o'xshash kutubxonalar ko'plab katta .so
# fayllarni xotiraga map qiladi va shu bilan katta virtual rezerv so'raydi — HAQIQIY
# ishlatilgan xotira esa bundan ancha kam bo'lishi mumkin. Shu sabab past qiymat
# (100MB, hatto 512MB ham) "failed to map segment from shared object" bilan importni
# butunlay buzib qo'yishi mumkin edi.
# Bu limitni ko'tarish REAL RAM sarfini oshirmaydi — Render'ning o'zi (masalan free
# tier'da odatda 512MB fizik RAM) haqiqiy xotira tugaganda process'ni baribir OOM-kill
# qiladi. Shuning uchun bu yerda katta (2048MB) qiymat qo'yish xavfsiz: bu faqat
# "kerak bo'lsa shuncha virtual joy band qilishga ruxsat" degani, kafolat emas.
BOT_MEMORY_LIMIT_MB = int(os.environ.get("BOT_MEMORY_LIMIT_MB", "2048"))
BOT_CPU_TIME_LIMIT_SEC = int(os.environ.get("BOT_CPU_TIME_LIMIT_SEC", "0"))  # 0 = cheklanmagan

DATA_DIR = os.path.join(os.path.dirname(__file__), "bots_data")
DB_PATH = os.path.join(os.path.dirname(__file__), "platform.db")

os.makedirs(DATA_DIR, exist_ok=True)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or user_id in SUPERADMIN_IDS


def is_superadmin(user_id: int) -> bool:
    return user_id in SUPERADMIN_IDS
