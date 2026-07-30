"""
services/crypto_utils.py

Muammo: bot tokenlari va ENV qiymatlari (bot_envs.value) SQLite'da plain text
saqlanardi, va bu fayl STORAGE_GROUP_ID guruhiga backup sifatida ham yuborilardi.
Ya'ni backup faylni ko'rgan har qanday odam barcha foydalanuvchilarning bot
tokenlarini ochiq holda ko'rar edi.

Yechim: har bir qiymat platform.db ga yozilishidan oldin ENCRYPTION_KEY (ENV)
yordamida Fernet (AES128-CBC + HMAC) bilan shifrlanadi, o'qishda deshifrlanadi.

ENCRYPTION_KEY ni generatsiya qilish uchun bir martalik buyruq:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Bu qiymatni Render → Environment → ENCRYPTION_KEY sifatida qo'shing va uni
HECH QACHON kod bilan birga git'ga commit qilmang.

DIQQAT: agar ENCRYPTION_KEY o'zgartirilsa yoki yo'qolsa, avval shifrlangan
eski qiymatlarni deshifrlab bo'lmaydi — kalitni ehtiyotkorlik bilan saqlang
(masalan Render Environment'ning o'zida, boshqa joyda nusxasi bilan).
"""
import os

from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY")
_fernet: Fernet | None = None

if _ENCRYPTION_KEY:
    _fernet = Fernet(_ENCRYPTION_KEY.encode())


def encrypt_value(plain: str) -> str:
    """ENCRYPTION_KEY berilmagan bo'lsa (masalan lokal test), shifrlamasdan qaytaradi —
    lekin production'da (Render) ENCRYPTION_KEY albatta o'rnatilishi kerak."""
    if _fernet is None:
        return plain
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_value(stored: str) -> str:
    if _fernet is None:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except InvalidToken:
        # ENCRYPTION_KEY o'rnatilgandan OLDIN yozilgan eski, shifrlanmagan qiymatlar uchun
        # orqaga qarab moslik (fallback) — xato tashlab, botni to'xtatib qo'ymaslik uchun.
        return stored
