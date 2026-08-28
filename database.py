"""
database.py
Foydalanuvchilar, deploy qilingan botlar va ENV o'zgaruvchilari uchun SQLite qatlami.

DIQQAT: Render Free Tier diski ephemeral (redeploy/restart'da o'chadi).
Shu sabab bu SQLite fayli faqat "runtime cache" sifatida ishlaydi.
Har bir muhim yozuvdan keyin (user ro'yxatdan o'tishi, bot deploy bo'lishi va h.k.)
platform.db faylining o'zi ham STORAGE_GROUP_ID guruhiga backup sifatida yuborilishi kerak
(services/file_utils.py -> backup_database() funksiyasi shuni qiladi).
Production'da buni tashqi Postgres (Supabase/Neon free tier) bilan almashtirish tavsiya etiladi.
"""
import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH, is_admin, STARS_PER_UNIT as DEFAULT_STARS_PER_UNIT, SECONDS_PER_UNIT as DEFAULT_SECONDS_PER_UNIT
from services.crypto_utils import encrypt_value, decrypt_value

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | denied
    created_at      INTEGER NOT NULL,
    approved_at     INTEGER,
    balance_stars   INTEGER NOT NULL DEFAULT 0,  -- Telegram Stars prepaid balans
    max_bots        INTEGER  -- admin belgilagan individual limit (NULL = global MAX_BOTS_PER_USER ishlatiladi)
);

CREATE TABLE IF NOT EXISTS pending_requests (
    request_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    message_text    TEXT,
    admin_msg_id    INTEGER,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bots (
    bot_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id        INTEGER NOT NULL,
    bot_username    TEXT,
    bot_token       TEXT,
    display_name    TEXT,                 -- foydalanuvchi yuklagan asl fayl nomi (kengaytmasiz)
    code_path       TEXT NOT NULL,       -- lokal disk yo'li (runtime uchun)
    storage_file_id TEXT,                -- STORAGE_GROUP_ID dagi original fayl (backup)
    is_zip          INTEGER NOT NULL DEFAULT 0,
    language        TEXT,
    build_cmd       TEXT,
    start_cmd       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'stopped',  -- running | stopped | crashed
    pid             INTEGER,
    created_at      INTEGER NOT NULL,
    deployed_by      INTEGER,             -- superadmin o'rniga deploy qilgan bo'lsa uning id'si
    stars_hosted    INTEGER NOT NULL DEFAULT 0,  -- 1 = admin tasdiqisiz, Stars balansidan hostlangan bot
    paid_until      INTEGER               -- stars_hosted bot uchun: shu vaqtgacha ishlash huquqi to'langan (epoch)
);

CREATE TABLE IF NOT EXISTS bot_envs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id          INTEGER NOT NULL,
    key             TEXT NOT NULL,
    value           TEXT NOT NULL,
    FOREIGN KEY (bot_id) REFERENCES bots(bot_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Eski (restore qilingan) bazalarda display_name ustuni bo'lmasligi mumkin — xavfsiz migratsiya
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN display_name TEXT")
        except sqlite3.OperationalError:
            pass  # ustun allaqachon mavjud
        try:
            conn.execute("ALTER TABLE users ADD COLUMN balance_stars INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # ustun allaqachon mavjud
        try:
            conn.execute("ALTER TABLE users ADD COLUMN max_bots INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN stars_hosted INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN paid_until INTEGER")
        except sqlite3.OperationalError:
            pass


# ---------- users ----------

def upsert_user(telegram_id: int, username: str, first_name: str):
    with get_conn() as conn:
        row = conn.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (telegram_id, username, first_name, status, created_at) VALUES (?,?,?,?,?)",
                (telegram_id, username, first_name, "pending", int(time.time())),
            )
        else:
            conn.execute(
                "UPDATE users SET username=?, first_name=? WHERE telegram_id=?",
                (username, first_name, telegram_id),
            )


def get_user(telegram_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()


def list_all_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()


def add_user_balance(telegram_id: int, delta_stars: int):
    """Balansga qo'shadi (yechish uchun manfiy son yuboring). Stars monetizatsiya
    funksiyasi (invoice/withdraw) implementatsiya qilinganda ishlatiladi."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET balance_stars = balance_stars + ? WHERE telegram_id=?",
            (delta_stars, telegram_id),
        )


def set_user_status(telegram_id: int, status: str):
    with get_conn() as conn:
        approved_at = int(time.time()) if status == "approved" else None
        conn.execute(
            "UPDATE users SET status=?, approved_at=COALESCE(?, approved_at) WHERE telegram_id=?",
            (status, approved_at, telegram_id),
        )


def get_user_balance(telegram_id: int) -> int:
    row = get_user(telegram_id)
    return row["balance_stars"] if row else 0


def set_user_max_bots(telegram_id: int, max_bots):
    """max_bots=None qilib qo'ysangiz, foydalanuvchi yana global MAX_BOTS_PER_USER'ga qaytadi."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET max_bots=? WHERE telegram_id=?", (max_bots, telegram_id))


def get_user_max_bots(telegram_id: int):
    row = get_user(telegram_id)
    return row["max_bots"] if row else None


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def get_stars_per_unit() -> int:
    """Superadmin panelidan o'zgartirilishi mumkin — DB'da yozilgan bo'lsa o'shani,
    aks holda config.py'dagi standart qiymatni qaytaradi (ENV o'zgartirish/redeploy shart emas)."""
    return int(get_setting("stars_per_unit", DEFAULT_STARS_PER_UNIT))


def get_seconds_per_unit() -> int:
    return int(get_setting("seconds_per_unit", DEFAULT_SECONDS_PER_UNIT))


def is_user_approved(telegram_id: int) -> bool:
    if is_admin(telegram_id):
        return True
    row = get_user(telegram_id)
    return bool(row and row["status"] == "approved")


# ---------- pending requests ----------

def create_pending_request(telegram_id: int, message_text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pending_requests (telegram_id, message_text, created_at) VALUES (?,?,?)",
            (telegram_id, message_text, int(time.time())),
        )
        return cur.lastrowid


def set_request_admin_msg(request_id: int, admin_msg_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE pending_requests SET admin_msg_id=? WHERE request_id=?", (admin_msg_id, request_id))


def get_request(request_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM pending_requests WHERE request_id=?", (request_id,)).fetchone()


# ---------- bots ----------

def count_user_bots(owner_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM bots WHERE owner_id=? AND status != 'deleted'", (owner_id,)
        ).fetchone()
        return row["c"]


def create_bot(owner_id, bot_username, bot_token, code_path, storage_file_id, is_zip, language,
                build_cmd, start_cmd, display_name=None, deployed_by=None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO bots
               (owner_id, bot_username, bot_token, code_path, storage_file_id, is_zip, language,
                build_cmd, start_cmd, display_name, status, created_at, deployed_by)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'stopped', ?, ?)""",
            (owner_id, bot_username, bot_token, code_path, storage_file_id, int(is_zip), language,
             build_cmd, start_cmd, display_name, int(time.time()), deployed_by),
        )
        return cur.lastrowid


def get_bot(bot_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM bots WHERE bot_id=?", (bot_id,)).fetchone()


def list_user_bots(owner_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bots WHERE owner_id=? AND status != 'deleted' ORDER BY created_at DESC", (owner_id,)
        ).fetchall()


def list_all_bots():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM bots WHERE status != 'deleted' ORDER BY created_at DESC").fetchall()


def set_bot_status(bot_id: int, status: str, pid: int = None):
    with get_conn() as conn:
        if pid is not None:
            conn.execute("UPDATE bots SET status=?, pid=? WHERE bot_id=?", (status, pid, bot_id))
        else:
            conn.execute("UPDATE bots SET status=? WHERE bot_id=?", (status, bot_id))


def set_bot_username(bot_id: int, username: str):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET bot_username=? WHERE bot_id=?", (username, bot_id))


def delete_bot(bot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET status='deleted' WHERE bot_id=?", (bot_id,))


def set_bot_stars_payment(bot_id: int, paid_until: int):
    """Botni stars_hosted deb belgilaydi va paid_until'ni (epoch) yozadi.
    Stars orqali deploy/uzaytirishda ishlatiladi."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE bots SET stars_hosted=1, paid_until=? WHERE bot_id=?",
            (paid_until, bot_id),
        )


def list_expired_stars_bots():
    """Hozir 'running' holatda turgan, lekin to'lov muddati o'tib ketgan stars_hosted botlar."""
    now = int(time.time())
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bots WHERE stars_hosted=1 AND status='running' AND paid_until IS NOT NULL AND paid_until <= ?",
            (now,),
        ).fetchall()


# ---------- envs ----------

def add_env(bot_id: int, key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bot_envs (bot_id, key, value) VALUES (?,?,?)",
            (bot_id, key, encrypt_value(value)),
        )


def list_envs(bot_id: int):
    """Bazadan o'qib, deshifrlangan (asl) qiymatlarni qaytaradi — chaqiruvchi tomon
    qiymatlar hali ham shifrlanganligi haqida qayg'urmasligi kerak."""
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM bot_envs WHERE bot_id=?", (bot_id,)).fetchall()
    return [{"key": r["key"], "value": decrypt_value(r["value"])} for r in rows]
