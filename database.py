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
import secrets
import sqlite3
import time
from contextlib import contextmanager

from config import DB_PATH, is_admin, STARS_PER_UNIT as DEFAULT_STARS_PER_UNIT, SECONDS_PER_UNIT as DEFAULT_SECONDS_PER_UNIT, MIN_WITHDRAW_STARS as DEFAULT_MIN_WITHDRAW_STARS, STORAGE_GROUP_ID as DEFAULT_STORAGE_GROUP_ID
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
    max_bots        INTEGER,  -- admin belgilagan individual limit (NULL = global MAX_BOTS_PER_USER ishlatiladi)
    approved_until  INTEGER,  -- admin tasdig'i orqali berilgan ruxsatning muddati (epoch) — majburiy, har approve'da beriladi
    is_banned       INTEGER NOT NULL DEFAULT 0,  -- 1 = host qilish huquqi vaqtincha olib tashlangan
    blocked_until   INTEGER,  -- to'lov qilgan (lifetime_topup_stars>0) userlar uchun avtomatik ochilish vaqti (epoch)
    lifetime_topup_stars INTEGER NOT NULL DEFAULT 0  -- umr bo'yi to'langan jami stars (balans sarflansa ham kamaymaydi — "haqiqiy to'lovchi"ligini bilish uchun)
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
    paid_until      INTEGER,              -- stars_hosted bot uchun: shu vaqtgacha ishlash huquqi to'langan (epoch)
    detected_credentials TEXT,            -- kod ichidan avtomatik topilgan token/ID'lar (JSON, admin uchun)
    is_test_clone   INTEGER NOT NULL DEFAULT 0,  -- 1 = admin shaxsiy test uchun boshqa bot kodidan clone qilgan nusxa
    github_url      TEXT,                 -- masalan https://github.com/owner/repo (faqat GitHub orqali deploy qilingan botlarda)
    github_branch   TEXT,                 -- deploy qilingan branch (masalan "main")
    webhook_secret  TEXT                  -- noyob token, webhook URL'ni tasdiqlash uchun (secrets.token_urlsafe)
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

CREATE TABLE IF NOT EXISTS stars_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    delta           INTEGER NOT NULL,   -- musbat = balansga qo'shildi, manfiy = yechildi
    reason          TEXT NOT NULL,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_providers (
    provider_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,        -- masalan "Cloudflare Workers AI", "OpenRouter"
    base_url        TEXT NOT NULL,        -- OpenAI-compatible /v1/chat/completions endpoint
    api_key         TEXT,                 -- shifrlangan (crypto_utils)
    model           TEXT NOT NULL,        -- masalan "@cf/qwen/qwen2.5-coder-32b-instruct"
    daily_limit     INTEGER NOT NULL DEFAULT 0,  -- kunlik so'rov limiti (0 = cheklovsiz)
    priority        INTEGER NOT NULL DEFAULT 100,  -- kichikroq = avval sinaladi
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id     INTEGER NOT NULL,
    telegram_id     INTEGER NOT NULL,
    bot_id          INTEGER,
    created_at      INTEGER NOT NULL,     -- kunlik limit shu ustun bo'yicha hisoblanadi
    success         INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (provider_id) REFERENCES ai_providers(provider_id)
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
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN detected_credentials TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN is_test_clone INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN github_url TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN github_branch TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN webhook_secret TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            # MUHIM FIX: requirements.txt (alohida .py fayl sifatida deploy qilingan
            # botlarda, yoki "requirements.txt almashtirish" oqimi orqali) ilgari
            # FAQAT workdir'ga (Render'ning ephemeral diskiga) yozilardi — hech qachon
            # Telegram STORAGE_GROUP'ga backup qilinmasdi. Natijada platforma qayta
            # ishga tushganda (restart/redeploy) faqat asosiy .py/.zip fayl tiklanardi,
            # requirements.txt esa BUTUNLAY yo'qolib qolardi (foydalanuvchiga "bot
            # requirements.txt'ni o'chirib yuboryapti" bo'lib ko'rinardi). Endi uning
            # ham alohida Telegram file_id'si saqlanadi va restore paytida tiklanadi.
            conn.execute("ALTER TABLE bots ADD COLUMN requirements_file_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            # YANGI FEATURE: har bot uchun "alohida disk" — botning butun workdir'i
            # (kodi + o'zi runtime'da yaratgan har qanday fayli: baza, JSON, medialar)
            # davriy ravishda shu file_id orqali backup qilinadi va restart'da tiklanadi.
            conn.execute("ALTER TABLE bots ADD COLUMN data_backup_file_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            # MUHIM FIX: ilgari yangi data-backup ESKISINI so'zsiz almashtirar edi —
            # agar yangi snapshot biror sababdan "bo'shab qolgan" holatni aks
            # ettirsa (masalan restart paytida hali birinchi backup ulgurmagan
            # bo'lsa), oldingi (yaxshi) versiya ustidan qaytarib bo'lmas darajada
            # yozilib ketardi. Endi bitta oldingi versiya ham alohida saqlanadi —
            # kerak bo'lsa qo'lda tiklab olish uchun.
            conn.execute("ALTER TABLE bots ADD COLUMN data_backup_file_id_prev TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            # YANGI FEATURE: alohida "Data Storage" supergruruh Topics (forum)
            # rejimida bo'lsa, har bot uchun ALOHIDA mavzu (topic) ochiladi —
            # shu botning barcha backup'lari faqat o'sha topic ichiga tushadi,
            # guruh ichida tartibli va admin uchun oson topiladigan bo'ladi.
            conn.execute("ALTER TABLE bots ADD COLUMN data_topic_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            # YANGI FEATURE: ba'zi child botlar o'z ichida webhook-server kodiga ega
            # (masalan Flask/aiohttp bilan). Ularga hosterbot orqali HAQIQIY webhook
            # imkoniyatini berish uchun — har bot uchun taxmin qilib bo'lmaydigan
            # tasodifiy token va proxy yoqilgan/o'chirilganligi shu yerda saqlanadi.
            conn.execute("ALTER TABLE bots ADD COLUMN webhook_proxy_token TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bots ADD COLUMN webhook_proxy_enabled INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN approved_until INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN blocked_until INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE users ADD COLUMN lifetime_topup_stars INTEGER NOT NULL DEFAULT 0")
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


def add_user_balance(telegram_id: int, delta_stars: int, reason: str = "o'zgarish"):
    """Balansga qo'shadi (yechish uchun manfiy son yuboring). Har bir o'zgarish
    stars_ledger jadvaliga ham yoziladi — foydalanuvchi keyinchalik "To'lov tarixi"
    orqali ko'ra oladi."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET balance_stars = balance_stars + ? WHERE telegram_id=?",
            (delta_stars, telegram_id),
        )
        conn.execute(
            "INSERT INTO stars_ledger (telegram_id, delta, reason, created_at) VALUES (?, ?, ?, ?)",
            (telegram_id, delta_stars, reason, int(time.time())),
        )


def get_user_ledger(telegram_id: int, limit: int = 20):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM stars_ledger WHERE telegram_id=? ORDER BY created_at DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


def get_user_by_username(username: str):
    username = username.lstrip("@")
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()


def search_users_by_prefix(prefix: str, limit: int = 8):
    """Inline QWERTY qidiruv uchun — username YOKI first_name boshi mos kelgan
    foydalanuvchilarni qaytaradi (qisman mos kelish, aniq teng emas). Bo'sh
    prefix uchun bo'sh ro'yxat qaytaradi — chunki "hamma foydalanuvchi" bu
    yerda ma'nosiz natija bo'lardi va performance uchun ham xavfli."""
    prefix = (prefix or "").strip().lstrip("@")
    if not prefix:
        return []
    like_pattern = prefix.replace("%", "\\%").replace("_", "\\_") + "%"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE (username LIKE ? ESCAPE '\\' COLLATE NOCASE) "
            "OR (first_name LIKE ? ESCAPE '\\' COLLATE NOCASE) "
            "ORDER BY created_at DESC LIMIT ?",
            (like_pattern, like_pattern, limit),
        ).fetchall()
        return rows


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


def set_user_approved_until(telegram_id: int, ts):
    with get_conn() as conn:
        conn.execute("UPDATE users SET approved_until=? WHERE telegram_id=?", (ts, telegram_id))


def get_user_approved_until(telegram_id: int):
    row = get_user(telegram_id)
    return row["approved_until"] if row else None


def list_expired_approvals():
    """Admin tomonidan berilgan muddat (approved_until) o'tib ketgan, hali ham
    'approved' holatdagi foydalanuvchilar — approval_expiry_watchdog shulardan
    ruxsatni avtomatik qaytarib oladi."""
    now = int(time.time())
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE status='approved' AND approved_until IS NOT NULL AND approved_until <= ?",
            (now,),
        ).fetchall()


def is_banned(telegram_id: int) -> bool:
    row = get_user(telegram_id)
    return bool(row["is_banned"]) if row else False


def set_user_banned(telegram_id: int, banned: bool):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_banned=? WHERE telegram_id=?", (1 if banned else 0, telegram_id))


def set_user_blocked_until(telegram_id: int, ts):
    with get_conn() as conn:
        conn.execute("UPDATE users SET blocked_until=? WHERE telegram_id=?", (ts, telegram_id))


def add_lifetime_topup(telegram_id: int, amount: int):
    """Balans qancha sarflansa ham kamaymaydigan, 'umr bo'yi qancha to'lagan'
    hisoblagichi — foydalanuvchi haqiqiy to'lovchi ekanini bilish uchun."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET lifetime_topup_stars = lifetime_topup_stars + ? WHERE telegram_id=?",
            (amount, telegram_id),
        )


def list_users_pending_auto_unblock():
    """blocked_until vaqti o'tib ketgan, hali ham bloklangan foydalanuvchilar."""
    now = int(time.time())
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE is_banned=1 AND blocked_until IS NOT NULL AND blocked_until <= ?",
            (now,),
        ).fetchall()


def get_unblock_min_stars() -> int:
    return int(get_setting("unblock_min_stars", 100))


def get_unblock_fee_percent() -> int:
    return int(get_setting("unblock_fee_percent", 10))


def get_block_default_hours() -> int:
    return int(get_setting("block_default_hours", 24))


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


def get_min_withdraw_stars() -> int:
    return int(get_setting("min_withdraw_stars", DEFAULT_MIN_WITHDRAW_STARS))


def get_data_storage_group_id() -> int:
    """YANGI FEATURE: botlar uchun 'disk' (data-backup) backup'lari qaysi
    Telegram supergruruhga tushishini superadmin panel orqali sozlash mumkin —
    agar hali sozlanmagan bo'lsa, ENG DASTLABKI (kod uchun ishlatiladigan)
    STORAGE_GROUP_ID'ga tushadi (eski xulq-atvor buzilmasligi uchun)."""
    return int(get_setting("data_storage_group_id", DEFAULT_STORAGE_GROUP_ID))


def local_port_for_bot(bot_id: int) -> int:
    """Har bot uchun DOIMIY, o'zaro to'qnashmaydigan mahalliy (faqat konteyner
    ichida ko'rinadigan) port — webhook-server kodi bor botlar shunga bog'lanadi.
    Tashqariga umuman ochiq emas — faqat hosterbot'ning o'zi (proxy orqali) unga murojaat qiladi."""
    return 20000 + (bot_id % 10000)


def enable_webhook_proxy(bot_id: int) -> str:
    """Bot uchun webhook-proxy'ni yoqadi. Token hali yo'q bo'lsa, tasodifiy
    (taxmin qilib bo'lmaydigan) token generatsiya qiladi. Qaytaradi: token."""
    import secrets
    with get_conn() as conn:
        row = conn.execute("SELECT webhook_proxy_token FROM bots WHERE bot_id=?", (bot_id,)).fetchone()
        token = row["webhook_proxy_token"] if row and row["webhook_proxy_token"] else secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE bots SET webhook_proxy_token=?, webhook_proxy_enabled=1 WHERE bot_id=?",
            (token, bot_id),
        )
        return token


def disable_webhook_proxy(bot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET webhook_proxy_enabled=0 WHERE bot_id=?", (bot_id,))


def get_bot_by_proxy_token(token: str):
    """Taxmin qilib bo'lmaydigan token orqali botni topadi — token noto'g'ri
    bo'lsa yoki proxy o'chirilgan bo'lsa None qaytaradi (boshqa birovning
    botiga so'rov yo'naltirilib qolmasligi uchun)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM bots WHERE webhook_proxy_token=? AND webhook_proxy_enabled=1", (token,),
        ).fetchone()
        return _decrypt_bot_row(row)


def set_data_topic_id(bot_id: int, topic_id: int):
    """Bot uchun data-storage guruhida ochilgan Topic (forum mavzu) ID'sini saqlaydi —
    shu bot uchun keyingi barcha backup'lar shu topic ichiga tushadi."""
    with get_conn() as conn:
        conn.execute("UPDATE bots SET data_topic_id=? WHERE bot_id=?", (topic_id, bot_id))


def is_user_approved(telegram_id: int) -> bool:
    if is_admin(telegram_id):
        return True
    row = get_user(telegram_id)
    return bool(row and row["status"] == "approved" and not row["is_banned"])


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
                build_cmd, start_cmd, display_name=None, deployed_by=None,
                github_url=None, github_branch=None, webhook_secret=None,
                requirements_file_id=None) -> int:
    # DIQQAT (xavfsizlik fix): bot_token bu yerda XOM Telegram bot tokeni bo'lishi mumkin
    # (masalan admin_panel.py'dagi test-deploy oqimida). bot_envs.value kabi bu ham
    # platform.db orqali STORAGE_GROUP_ID guruhiga backup qilinadi, shu sabab bot_envs bilan
    # bir xil qoidaga bo'ysunishi kerak: ENCRYPTION_KEY bo'lsa shifrlab saqlaymiz.
    encrypted_token = encrypt_value(bot_token) if bot_token else bot_token
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO bots
               (owner_id, bot_username, bot_token, code_path, storage_file_id, is_zip, language,
                build_cmd, start_cmd, display_name, status, created_at, deployed_by,
                github_url, github_branch, webhook_secret, requirements_file_id)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'stopped', ?, ?, ?, ?, ?, ?)""",
            (owner_id, bot_username, encrypted_token, code_path, storage_file_id, int(is_zip), language,
             build_cmd, start_cmd, display_name, int(time.time()), deployed_by,
             github_url, github_branch, webhook_secret, requirements_file_id),
        )
        return cur.lastrowid


def set_bot_github_link(bot_id: int, github_url: str, github_branch: str, webhook_secret: str):
    """Mavjud botni GitHub repo'ga bog'laydi (masalan 'Kodni almashtirish' orqali
    GitHub havolasi yuborilganda) — shundan keyin bot ham push-webhook orqali
    avtomatik qayta deploy imkoniyatiga, ham platforma restart'da GitHub'dan
    qayta tiklanish imkoniyatiga ega bo'ladi (storage_file_id yo'q/eskirgan bo'lsa ham)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE bots SET github_url=?, github_branch=?, webhook_secret=? WHERE bot_id=?",
            (github_url, github_branch, webhook_secret, bot_id),
        )


def set_storage_file_id(bot_id: int, file_id: str, is_zip: bool = None):
    """Botning asosiy kodi (.py/.zip) qayta backup qilingandan keyin uning yangi
    file_id'sini saqlaydi — platforma qayta ishga tushganda (restore_running_bots)
    ESKI emas, aynan shu ENG SO'NGGI backup orqali tiklanadi.

    MUHIM FIX: ilgari "📄 Kodni almashtirish" (fix_code) orqali kod tahrirlansa,
    yangi kod FAQAT workdir'ga (ephemeral diskka) yozilardi, storage_file_id esa
    hamon ILK deploydagi eski faylga ishora qilib qolardi. Natijada platforma
    restart bo'lganda (Render spin-down/redeploy) bot yana ESKI kod bilan
    tiklanardi — go'yo tahrirlash "bekor bo'lganday" ko'rinardi.
    """
    with get_conn() as conn:
        if is_zip is None:
            conn.execute("UPDATE bots SET storage_file_id=? WHERE bot_id=?", (file_id, bot_id))
        else:
            conn.execute(
                "UPDATE bots SET storage_file_id=?, is_zip=? WHERE bot_id=?",
                (file_id, int(is_zip), bot_id),
            )


def set_data_backup_file_id(bot_id: int, file_id: str):
    """Botning to'liq workdir-snapshot (data) backup'i yangilangandan keyin file_id'sini saqlaydi.

    MUHIM FIX: eskisini yo'qotmaslik uchun avval uni data_backup_file_id_prev'ga
    ko'chiramiz — shu bilan kamida BITTA oldingi versiya har doim tiklab olish
    uchun mavjud bo'ladi (masalan yangi snapshot kutilmaganda "bo'shab qolgan"
    holatni aks ettirsa)."""
    with get_conn() as conn:
        row = conn.execute("SELECT data_backup_file_id FROM bots WHERE bot_id=?", (bot_id,)).fetchone()
        prev = row["data_backup_file_id"] if row else None
        conn.execute(
            "UPDATE bots SET data_backup_file_id_prev=?, data_backup_file_id=? WHERE bot_id=?",
            (prev, file_id, bot_id),
        )


def set_requirements_file_id(bot_id: int, file_id: str):
    """requirements.txt Telegram STORAGE_GROUP'ga backup qilingandan keyin, uning
    file_id'sini saqlaydi — platforma qayta ishga tushganda (restore_running_bots)
    shu file_id orqali requirements.txt qayta tiklanadi."""
    with get_conn() as conn:
        conn.execute("UPDATE bots SET requirements_file_id=? WHERE bot_id=?", (file_id, bot_id))


def _decrypt_bot_row(row):
    """sqlite3.Row o'zgarmas (immutable) bo'lgani uchun dict'ga o'girib, bot_token'ni
    deshifrlab qaytaramiz. Chaqiruvchi tomon hamon row["bot_token"] kabi ishlata oladi."""
    if row is None:
        return None
    d = dict(row)
    if d.get("bot_token"):
        d["bot_token"] = decrypt_value(d["bot_token"])
    return d


def get_bot(bot_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bots WHERE bot_id=?", (bot_id,)).fetchone()
        return _decrypt_bot_row(row)


def transfer_bot_owner(bot_id: int, new_owner_id: int):
    """Botning owner_id'sini boshqa foydalanuvchiga o'tkazadi (egasini almashtirish).
    Chaqiruvchi (handler) yangi egasining mavjudligini/holatini (approved, ban
    emasligini va h.k.) OLDINDAN tekshirishi kerak — bu funksiya faqat DB
    yozuvini yangilaydi, biznes-qoidalarni tekshirmaydi."""
    with get_conn() as conn:
        conn.execute("UPDATE bots SET owner_id=? WHERE bot_id=?", (new_owner_id, bot_id))


def get_bot_by_webhook(bot_id: int, webhook_secret: str):
    """GitHub webhook so'rovi kelganda, URL'dagi bot_id+secret ikkalasi ham
    to'g'ri mos kelgan botni topadi (secret noto'g'ri bo'lsa None qaytaradi —
    bu boshqa odamning botini tasodifiy qayta deploy qilishdan himoya)."""
    bot_row = get_bot(bot_id)
    if bot_row is None or not bot_row.get("webhook_secret"):
        return None
    # constant-time solishtirish — timing attack orqali secret'ni belgi-belgilab
    # tахmin qilishning oldini olish uchun (oddiy != operatori birinchi mos
    # kelmagan belgida qisqa tuxtaydi va bu vaqt farqi orqali sizib chiqishi mumkin edi)
    if not secrets.compare_digest(bot_row["webhook_secret"], webhook_secret):
        return None
    return bot_row


def list_user_bots(owner_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM bots WHERE owner_id=? AND status != 'deleted' ORDER BY created_at DESC", (owner_id,)
        ).fetchall()
        return [_decrypt_bot_row(r) for r in rows]


def list_all_bots():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM bots WHERE status != 'deleted' ORDER BY created_at DESC").fetchall()
        return [_decrypt_bot_row(r) for r in rows]


def set_bot_status(bot_id: int, status: str, pid: int = None):
    with get_conn() as conn:
        if pid is not None:
            conn.execute("UPDATE bots SET status=?, pid=? WHERE bot_id=?", (status, pid, bot_id))
        else:
            conn.execute("UPDATE bots SET status=? WHERE bot_id=?", (status, bot_id))


def set_bot_username(bot_id: int, username: str):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET bot_username=? WHERE bot_id=?", (username, bot_id))


def set_bot_detected_credentials(bot_id: int, json_text: str):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET detected_credentials=? WHERE bot_id=?", (json_text, bot_id))


def set_bot_code_path(bot_id: int, code_path: str):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET code_path=? WHERE bot_id=?", (code_path, bot_id))


def set_bot_display_name(bot_id: int, display_name: str):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET display_name=? WHERE bot_id=?", (display_name, bot_id))


def mark_bot_test_clone(bot_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE bots SET is_test_clone=1 WHERE bot_id=?", (bot_id,))


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
        rows = conn.execute(
            "SELECT * FROM bots WHERE stars_hosted=1 AND status='running' AND paid_until IS NOT NULL AND paid_until <= ?",
            (now,),
        ).fetchall()
        return [_decrypt_bot_row(r) for r in rows]


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


def upsert_env(bot_id: int, key: str, value: str):
    """FixEnv oqimi uchun: agar shu KEY bilan ENV bo'lsa qiymatini yangilaydi,
    bo'lmasa yangi qator qo'shadi — foydalanuvchi bir xil KEY'ni ikki marta
    yozib, duplikat yaratib qo'ymasligi uchun."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM bot_envs WHERE bot_id=? AND key=?", (bot_id, key)
        ).fetchone()
        if existing:
            conn.execute("UPDATE bot_envs SET value=? WHERE id=?", (encrypt_value(value), existing["id"]))
        else:
            conn.execute(
                "INSERT INTO bot_envs (bot_id, key, value) VALUES (?,?,?)",
                (bot_id, key, encrypt_value(value)),
            )


def delete_env(bot_id: int, key: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM bot_envs WHERE bot_id=? AND key=?", (bot_id, key))


# ---------- AI providers (superadmin: bir nechta AI API qo'shish, ustuvorlik/fallback) ----------

def create_ai_provider(name: str, base_url: str, api_key: str, model: str,
                        daily_limit: int = 0, priority: int = 100) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO ai_providers (name, base_url, api_key, model, daily_limit, priority, is_active, created_at)
               VALUES (?,?,?,?,?,?,1,?)""",
            (name, base_url, encrypt_value(api_key) if api_key else None, model,
             daily_limit, priority, int(time.time())),
        )
        return cur.lastrowid


def _decrypt_provider_row(row):
    if row is None:
        return None
    d = dict(row)
    if d.get("api_key"):
        d["api_key"] = decrypt_value(d["api_key"])
    return d


def get_ai_provider(provider_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_providers WHERE provider_id=?", (provider_id,)).fetchone()
        return _decrypt_provider_row(row)


def list_ai_providers(active_only: bool = False):
    """priority bo'yicha o'sish tartibida (kichikroq = avval sinaladi) qaytaradi —
    services/ai_client.py shu tartibda birma-bir provayderlarni sinab ko'radi."""
    query = "SELECT * FROM ai_providers"
    if active_only:
        query += " WHERE is_active=1"
    query += " ORDER BY priority ASC, provider_id ASC"
    with get_conn() as conn:
        rows = conn.execute(query).fetchall()
        return [_decrypt_provider_row(r) for r in rows]


def set_ai_provider_active(provider_id: int, active: bool):
    with get_conn() as conn:
        conn.execute("UPDATE ai_providers SET is_active=? WHERE provider_id=?", (1 if active else 0, provider_id))


def delete_ai_provider(provider_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM ai_providers WHERE provider_id=?", (provider_id,))
        conn.execute("DELETE FROM ai_usage_log WHERE provider_id=?", (provider_id,))


def get_ai_provider_usage_today(provider_id: int) -> int:
    """Shu provayder uchun bugungi (UTC 00:00'dan buyon) muvaffaqiyatli
    so'rovlar soni — Cloudflare'ning o'zi ham kunlik hisobini 00:00 UTC'da
    tiklaydi, shu bilan bir xil chegarani ishlatamiz (chalkashlik bo'lmasin)."""
    today_utc = time.strftime("%Y-%m-%d", time.gmtime())
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM ai_usage_log WHERE provider_id=? AND success=1 "
            "AND strftime('%Y-%m-%d', created_at, 'unixepoch') = ?",
            (provider_id, today_utc),
        ).fetchone()
        return row["c"] if row else 0


def log_ai_usage(provider_id: int, telegram_id: int, bot_id: int, success: bool = True):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_usage_log (provider_id, telegram_id, bot_id, created_at, success) VALUES (?,?,?,?,?)",
            (provider_id, telegram_id, bot_id, int(time.time()), 1 if success else 0),
        )


def get_ai_help_price_stars() -> int:
    """Superadmin panelidan o'zgartirilishi mumkin — bir marta AI crash-tashxis
    so'rashning Stars narxi. config.py'da standart yo'q, shu sabab kod ichida
    to'g'ridan-to'g'ri default beriladi."""
    return int(get_setting("ai_help_price_stars", 5))


def get_ai_chat_price_stars() -> int:
    """Erkin AI suhbat rejimida (handlers/ai_chat.py) har bir xabar uchun narx —
    crash-tashxisdan (get_ai_help_price_stars) alohida, odatda arzonroq bo'ladi
    (default 1⭐️), chunki bu shunchaki savol-javob, tashxis emas. AI haqiqatan
    fayl tahrirlashni taklif qilib, foydalanuvchi tasdiqlasa, QO'SHIMCHA ravishda
    get_ai_help_price_stars() narxi ham yechiladi (jami ikkisi qo'shiladi)."""
    return int(get_setting("ai_chat_price_stars", 1))


# ---------- Superadmin dashboard: umumiy statistika ----------

def get_dashboard_stats() -> dict:
    """Superadmin '📊 Umumiy statistika' bo'limi uchun barcha asosiy raqamlarni
    bitta so'rovlar to'plamida yig'ib beradi. Faqat SUPERADMIN_IDS ko'radigan
    joyda ishlatiladi — bu yerdagi raqamlar (jami foydalanuvchi, bugungi yangi
    bot va h.k.) boshqa hech kimga ko'rsatilmaydi."""
    now = int(time.time())
    day_ago = now - 86400
    week_ago = now - 7 * 86400
    today_str = time.strftime("%Y-%m-%d", time.gmtime())

    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        approved_users = conn.execute("SELECT COUNT(*) as c FROM users WHERE status='approved'").fetchone()["c"]
        pending_users = conn.execute("SELECT COUNT(*) as c FROM users WHERE status='pending'").fetchone()["c"]
        banned_users = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned=1").fetchone()["c"]
        new_users_today = conn.execute(
            "SELECT COUNT(*) as c FROM users WHERE created_at >= ?", (day_ago,)
        ).fetchone()["c"]

        total_bots = conn.execute("SELECT COUNT(*) as c FROM bots WHERE status != 'deleted'").fetchone()["c"]
        running_bots = conn.execute("SELECT COUNT(*) as c FROM bots WHERE status='running'").fetchone()["c"]
        crashed_bots = conn.execute("SELECT COUNT(*) as c FROM bots WHERE status='crashed'").fetchone()["c"]
        stars_hosted_bots = conn.execute(
            "SELECT COUNT(*) as c FROM bots WHERE stars_hosted=1 AND status != 'deleted'"
        ).fetchone()["c"]
        deploys_today = conn.execute(
            "SELECT COUNT(*) as c FROM bots WHERE created_at >= ?", (day_ago,)
        ).fetchone()["c"]
        deploys_week = conn.execute(
            "SELECT COUNT(*) as c FROM bots WHERE created_at >= ?", (week_ago,)
        ).fetchone()["c"]

        total_topup_stars = conn.execute(
            "SELECT COALESCE(SUM(lifetime_topup_stars), 0) as s FROM users"
        ).fetchone()["s"]

        ai_calls_today = conn.execute(
            "SELECT COUNT(*) as c FROM ai_usage_log WHERE success=1 "
            "AND strftime('%Y-%m-%d', created_at, 'unixepoch') = ?",
            (today_str,),
        ).fetchone()["c"]

        top_ram_rows = conn.execute(
            "SELECT bot_id, bot_username, display_name FROM bots WHERE status='running' LIMIT 200"
        ).fetchall()

    return {
        "total_users": total_users,
        "approved_users": approved_users,
        "pending_users": pending_users,
        "banned_users": banned_users,
        "new_users_today": new_users_today,
        "total_bots": total_bots,
        "running_bots": running_bots,
        "crashed_bots": crashed_bots,
        "stars_hosted_bots": stars_hosted_bots,
        "deploys_today": deploys_today,
        "deploys_week": deploys_week,
        "total_topup_stars": total_topup_stars,
        "ai_calls_today": ai_calls_today,
        "running_bot_rows": [dict(r) for r in top_ram_rows],
    }
