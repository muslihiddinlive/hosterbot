import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("STORAGE_GROUP_ID", "-1001")

from services.deploy_manager import normalize_interpreter, static_scan
from services.deploy_log import write_stage
from services import crypto_utils


def test_normalize_interpreter_pip():
    result = normalize_interpreter("pip install -r requirements.txt")
    assert result == f'"{sys.executable}" -m pip install -r requirements.txt'


def test_normalize_interpreter_python():
    result = normalize_interpreter("python bot.py")
    assert result == f'"{sys.executable}" bot.py'


def test_normalize_interpreter_python3():
    result = normalize_interpreter("python3 bot.py --flag")
    assert result == f'"{sys.executable}" bot.py --flag'


def test_normalize_interpreter_leaves_other_commands_untouched():
    assert normalize_interpreter("node index.js") == "node index.js"
    assert normalize_interpreter("") == ""


def test_normalize_interpreter_does_not_touch_python_mid_string():
    # "python" so'zi qatorning O'RTASIDA bo'lsa tegilmasligi kerak
    cmd = "echo python is great"
    assert normalize_interpreter(cmd) == cmd


def test_static_scan_detects_new_patterns():
    code = "import socket\ns = socket.socket()\nos.remove('/etc/passwd')"
    warnings = static_scan(code)
    assert any("socket.socket(" in w for w in warnings)


def test_static_scan_clean_code_no_warnings():
    code = "print('hello')\nimport requests\n"
    assert static_scan(code) == []


def test_write_stage_format():
    buf = io.StringIO()
    write_stage(buf, "build_ok", exit_code=0, cmd="pip install -r requirements.txt")
    line = buf.getvalue()
    assert line.startswith("::STAGE:: ")
    assert "build_ok" in line
    assert 'exit_code="0"' in line
    assert line.endswith("\n")


def test_crypto_roundtrip_with_key():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    os.environ["ENCRYPTION_KEY"] = key
    import importlib
    importlib.reload(crypto_utils)
    encrypted = crypto_utils.encrypt_value("my-secret-token")
    assert encrypted != "my-secret-token"
    assert crypto_utils.decrypt_value(encrypted) == "my-secret-token"
    del os.environ["ENCRYPTION_KEY"]
    importlib.reload(crypto_utils)


def test_crypto_passthrough_without_key():
    import importlib
    os.environ.pop("ENCRYPTION_KEY", None)
    importlib.reload(crypto_utils)
    assert crypto_utils.encrypt_value("plain") == "plain"
    assert crypto_utils.decrypt_value("plain") == "plain"


def test_default_memory_limit_supports_heavy_libs(monkeypatch):
    # opencv (ffmpeg kodekli)/numpy kabi og'ir kutubxonalar uchun 512MB ham Render'da
    # yetarli emasligi aniqlandi (real log orqali) — default ancha ko'tarilgan
    monkeypatch.delenv("BOT_MEMORY_LIMIT_MB", raising=False)
    import importlib
    import config
    importlib.reload(config)
    assert config.BOT_MEMORY_LIMIT_MB >= 2048


class _FakeBotSuccess:
    async def send_document(self, *a, **kw):
        class Sent:
            message_id = 1
        return Sent()

    async def pin_chat_message(self, *a, **kw):
        return True


class _FakeBotFailure:
    async def send_document(self, *a, **kw):
        raise RuntimeError("Chat not found")


async def _run_backup_test(fake_bot):
    from services.backup import backup_database
    return await backup_database(fake_bot)


def test_backup_database_returns_ok_tuple_on_success():
    import asyncio
    ok, error = asyncio.run(_run_backup_test(_FakeBotSuccess()))
    assert ok is True
    assert error is None


def test_backup_database_returns_error_tuple_on_failure():
    import asyncio
    ok, error = asyncio.run(_run_backup_test(_FakeBotFailure()))
    assert ok is False
    assert "Chat not found" in error


class _FakeBotMigrated:
    async def send_document(self, *a, **kw):
        from aiogram.exceptions import TelegramMigrateToChat
        from aiogram.methods import SendDocument
        raise TelegramMigrateToChat(
            method=SendDocument(chat_id=1, document="x"),
            message="group migrated",
            migrate_to_chat_id=-1004480579801,
        )


def test_backup_database_reports_new_chat_id_on_migration():
    import asyncio
    ok, error = asyncio.run(_run_backup_test(_FakeBotMigrated()))
    assert ok is False
    assert "-1004480579801" in error


def test_python_version_pinned_to_stable_release():
    # python-telegram-bot==21.6 kabi kutubxonalar Python 3.14 (asyncio o'zgarishlari
    # tufayli) bilan ishlamaydi - shu sabab barqaror versiya repo'ga michlab qo'yilgan
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".python-version")) as f:
        version = f.read().strip()
    major, minor = (int(x) for x in version.split(".")[:2])
    assert (major, minor) < (3, 14), ".python-version 3.14+ ga o'tkazilgan - PTB 21.x buzilishi mumkin"


def _fresh_db(tmp_path, monkeypatch):
    """Har bir testga alohida, bo'sh platform.db beradi (davlat testlar orasida
    sizib qolmasligi uchun) va bot_envs bilan bir xil ENCRYPTION_KEY ni yoqadi."""
    import importlib
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    import config
    importlib.reload(config)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "platform_test.db"))

    import services.crypto_utils as crypto_utils_mod
    importlib.reload(crypto_utils_mod)

    import database as db_mod
    importlib.reload(db_mod)
    db_mod.init_db()
    return db_mod


def test_bot_token_encrypted_at_rest(tmp_path, monkeypatch):
    # Xavfsizlik fix: bots.bot_token endi bot_envs.value bilan bir xil qoidaga
    # bo'ysunadi - ENCRYPTION_KEY bo'lsa, xom holda emas, shifrlangan holda saqlanadi.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    raw_token = "123456:AA-real-looking-secret-token"

    bot_id = db_mod.create_bot(
        owner_id=1, bot_username=None, bot_token=raw_token, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )

    with db_mod.get_conn() as conn:
        stored = conn.execute("SELECT bot_token FROM bots WHERE bot_id=?", (bot_id,)).fetchone()["bot_token"]
    assert stored != raw_token, "bot_token bazada xom (shifrlanmagan) holda saqlanmoqda"

    fetched = db_mod.get_bot(bot_id)
    assert fetched["bot_token"] == raw_token, "get_bot() orqali deshifrlangan asl token qaytishi kerak"

    listed = db_mod.list_all_bots()
    assert listed[0]["bot_token"] == raw_token, "list_all_bots() orqali ham deshifrlangan token qaytishi kerak"


def test_bot_token_none_stays_none(tmp_path, monkeypatch):
    # bot_token=None bo'lgan (odatiy) hollarda encrypt/decrypt orqali xato chiqmasligi kerak.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username=None, bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    fetched = db_mod.get_bot(bot_id)
    assert fetched["bot_token"] is None
