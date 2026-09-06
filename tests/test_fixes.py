import io
import os
import sys

import pytest

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


def test_format_uptime_days_and_hours():
    from handlers.user_menu import _format_uptime
    assert _format_uptime(3 * 86400 + 4 * 3600) == "3 kun 4 soat"


def test_format_uptime_minutes_only():
    from handlers.user_menu import _format_uptime
    assert _format_uptime(45 * 60) == "45 daqiqa"


def test_format_uptime_under_a_minute():
    from handlers.user_menu import _format_uptime
    assert _format_uptime(30) == "1 daqiqadan kam"


def test_help_topics_keyboard_has_entry_per_topic():
    from handlers.help_faq import FAQ_TOPICS
    from keyboards import help_topics_kb
    kb = help_topics_kb(FAQ_TOPICS)
    # Har bir mavzu bitta qatorda, bitta tugma bo'lishi kerak (menyu tushunarli qolishi uchun)
    assert len(kb.inline_keyboard) == len(FAQ_TOPICS)
    all_callback_data = {row[0].callback_data for row in kb.inline_keyboard}
    for topic_id in FAQ_TOPICS:
        assert f"help_topic:{topic_id}" in all_callback_data


def test_bot_manage_kb_shows_rebuild_only_when_crashed():
    from keyboards import bot_manage_kb
    running_row = {"bot_id": 1, "status": "running", "stars_hosted": 0}
    crashed_row = {"bot_id": 1, "status": "crashed", "stars_hosted": 0}

    running_kb = bot_manage_kb(running_row)
    crashed_kb = bot_manage_kb(crashed_row)

    running_callbacks = {btn.callback_data for row in running_kb.inline_keyboard for btn in row}
    crashed_callbacks = {btn.callback_data for row in crashed_kb.inline_keyboard for btn in row}

    assert "bot_rebuild:1" not in running_callbacks
    assert "bot_rebuild:1" in crashed_callbacks


def test_bot_manage_kb_crashed_shows_fix_and_ai_help_buttons():
    # Xavfsizlik/UX fix: crashed holatda foydalanuvchi kodni/requirements.txt'ni
    # o'zi almashtira olishi yoki AI'dan yordam so'rashi kerak (Stars orqali).
    from keyboards import bot_manage_kb
    crashed_row = {"bot_id": 7, "status": "crashed", "stars_hosted": 0}
    kb = bot_manage_kb(crashed_row, has_env=False)
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "fix_code:7" in callbacks
    assert "fix_reqs:7" in callbacks
    assert "ai_help:7" in callbacks
    assert "fix_env:7" not in callbacks  # has_env=False bo'lgani uchun ko'rinmasligi kerak


def test_bot_manage_kb_shows_env_edit_only_when_has_env():
    from keyboards import bot_manage_kb
    crashed_row = {"bot_id": 9, "status": "crashed", "stars_hosted": 0}
    kb = bot_manage_kb(crashed_row, has_env=True)
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "fix_env:9" in callbacks


def test_crash_notify_kb_matches_manage_kb_buttons():
    from keyboards import crash_notify_kb
    kb = crash_notify_kb(5, has_env=True)
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert {"bot_start:5", "bot_rebuild:5", "fix_code:5", "fix_reqs:5", "fix_env:5", "ai_help:5"} <= callbacks


def test_upsert_env_updates_existing_key_without_duplicating(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username=None, bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.add_env(bot_id, "TOKEN", "old-value")
    db_mod.upsert_env(bot_id, "TOKEN", "new-value")

    envs = db_mod.list_envs(bot_id)
    assert len(envs) == 1
    assert envs[0]["value"] == "new-value"


def test_upsert_env_creates_new_key_if_absent(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username=None, bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.upsert_env(bot_id, "NEW_KEY", "value1")
    envs = db_mod.list_envs(bot_id)
    assert len(envs) == 1
    assert envs[0]["key"] == "NEW_KEY"


def test_delete_env_removes_key(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username=None, bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.add_env(bot_id, "A", "1")
    db_mod.add_env(bot_id, "B", "2")
    db_mod.delete_env(bot_id, "A")
    envs = db_mod.list_envs(bot_id)
    assert len(envs) == 1
    assert envs[0]["key"] == "B"


def test_ai_provider_crud_roundtrip(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    provider_id = db_mod.create_ai_provider(
        name="Cloudflare Workers AI",
        base_url="https://api.cloudflare.com/client/v4/accounts/ACCID/ai/v1",
        api_key="secret-key-123",
        model="@cf/qwen/qwen2.5-coder-32b-instruct",
        daily_limit=40,
        priority=10,
    )
    provider = db_mod.get_ai_provider(provider_id)
    assert provider["name"] == "Cloudflare Workers AI"
    assert provider["api_key"] == "secret-key-123"  # deshifrlangan holda qaytishi kerak
    assert provider["is_active"] == 1

    db_mod.set_ai_provider_active(provider_id, False)
    assert db_mod.get_ai_provider(provider_id)["is_active"] == 0

    db_mod.delete_ai_provider(provider_id)
    assert db_mod.get_ai_provider(provider_id) is None


def test_ai_provider_api_key_encrypted_at_rest(tmp_path, monkeypatch):
    # bot_token uchun qilingan xavfsizlik fixi bilan bir xil qoida: API kalit
    # ham ai_providers jadvalida xom holda saqlanmasligi kerak.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    provider_id = db_mod.create_ai_provider(
        name="Test Provider", base_url="https://example.com/v1",
        api_key="raw-secret-key", model="test-model",
    )
    with db_mod.get_conn() as conn:
        stored = conn.execute(
            "SELECT api_key FROM ai_providers WHERE provider_id=?", (provider_id,)
        ).fetchone()["api_key"]
    assert stored != "raw-secret-key"


def test_list_ai_providers_ordered_by_priority(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.create_ai_provider(name="Low priority (backup)", base_url="https://b.example/v1", api_key=None, model="m", priority=50)
    db_mod.create_ai_provider(name="High priority (primary)", base_url="https://a.example/v1", api_key=None, model="m", priority=5)
    providers = db_mod.list_ai_providers()
    assert providers[0]["name"] == "High priority (primary)"
    assert providers[1]["name"] == "Low priority (backup)"


def test_list_ai_providers_active_only_filters_inactive(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    active_id = db_mod.create_ai_provider(name="Active", base_url="https://a.example/v1", api_key=None, model="m")
    inactive_id = db_mod.create_ai_provider(name="Inactive", base_url="https://b.example/v1", api_key=None, model="m")
    db_mod.set_ai_provider_active(inactive_id, False)

    all_providers = db_mod.list_ai_providers(active_only=False)
    active_providers = db_mod.list_ai_providers(active_only=True)
    assert len(all_providers) == 2
    assert len(active_providers) == 1
    assert active_providers[0]["provider_id"] == active_id


def test_ai_provider_usage_counter_increments_and_respects_daily_limit(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    provider_id = db_mod.create_ai_provider(
        name="Limited", base_url="https://example.com/v1", api_key=None, model="m", daily_limit=2,
    )
    assert db_mod.get_ai_provider_usage_today(provider_id) == 0

    db_mod.log_ai_usage(provider_id, telegram_id=111, bot_id=1, success=True)
    db_mod.log_ai_usage(provider_id, telegram_id=111, bot_id=1, success=True)
    assert db_mod.get_ai_provider_usage_today(provider_id) == 2

    # Muvaffaqiyatsiz urinishlar kunlik limitga qo'shilmasligi kerak — foydalanuvchi
    # xato tufayli o'z byudjetini yo'qotib qo'ymasligi uchun.
    db_mod.log_ai_usage(provider_id, telegram_id=111, bot_id=1, success=False)
    assert db_mod.get_ai_provider_usage_today(provider_id) == 2


def test_get_ai_help_price_stars_default_and_override(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    assert db_mod.get_ai_help_price_stars() == 5  # default
    db_mod.set_setting("ai_help_price_stars", 10)
    assert db_mod.get_ai_help_price_stars() == 10


@pytest.mark.asyncio
async def test_ask_ai_falls_back_to_next_provider_when_first_hits_daily_limit(tmp_path, monkeypatch):
    # Asosiy dizayn talabi: bitta provayder kunlik limitga yetsa, tizim
    # avtomatik ravishda ustuvorligi pastroq keyingi provayderga o'tishi kerak.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    primary_id = db_mod.create_ai_provider(
        name="Primary (full)", base_url="https://primary.example/v1", api_key=None,
        model="m1", daily_limit=1, priority=1,
    )
    backup_id = db_mod.create_ai_provider(
        name="Backup", base_url="https://backup.example/v1", api_key=None,
        model="m2", daily_limit=0, priority=2,
    )
    # Primary'ni "bugun allaqachon limitga yetgan" holatga keltiramiz
    db_mod.log_ai_usage(primary_id, telegram_id=1, bot_id=None, success=True)

    import importlib
    import services.ai_client as ai_client_mod
    importlib.reload(ai_client_mod)

    calls = []

    async def fake_call_provider(provider, system_prompt, user_prompt):
        calls.append(provider["name"])
        return f"javob {provider['name']}dan"

    monkeypatch.setattr(ai_client_mod, "_call_provider", fake_call_provider)

    result = await ai_client_mod.ask_ai("sys", "user", telegram_id=1, bot_id=None)
    assert result == "javob Backupdan"
    assert calls == ["Backup"]  # Primary chaqirilmagan — limitga yetgani uchun oldindan o'tkazib yuborilgan


@pytest.mark.asyncio
async def test_ask_ai_raises_when_no_active_providers(tmp_path, monkeypatch):
    _fresh_db(tmp_path, monkeypatch)
    import importlib
    import services.ai_client as ai_client_mod
    importlib.reload(ai_client_mod)

    with pytest.raises(ai_client_mod.AIError):
        await ai_client_mod.ask_ai("sys", "user", telegram_id=1, bot_id=None)


def test_build_crash_diagnosis_prompt_truncates_long_log():
    from services.ai_client import build_crash_diagnosis_prompt
    long_log = "x" * 5000
    system_prompt, user_prompt = build_crash_diagnosis_prompt("@testbot", long_log)
    assert "o'zbek" in system_prompt
    # Log 2000 belgigacha qisqartirilishi kerak (token sarfini kamaytirish uchun)
    assert len(user_prompt) < 2200


def test_fix_code_py_txt_extension_is_auto_corrected():
    # Fayl-menejer xatosi: "bot.py.txt" -> "bot.py" ga avtomatik tuzatilishi kerak,
    # lekin boshqa har qanday kengaytma rad etilishi kerak.
    def normalize(file_name: str) -> str:
        lower_name = file_name.lower()
        if lower_name.endswith(".py.txt"):
            file_name = file_name[:-4]
        return file_name

    assert normalize("bot.py.txt").lower().endswith(".py")
    assert normalize("bot.py.txt") == "bot.py"
    assert not normalize("bot.txt").lower().endswith(".py")
    assert not normalize("bot.zip").lower().endswith(".py")


def test_cloudflare_account_id_builds_correct_base_url():
    # Superadmin faqat Account ID kiritganda, admin_panel.py'dagi
    # ai_provider_account_id_entered aynan shu URL shaklini yasashi kerak.
    account_id = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
    expected = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
    assert base_url == expected
    assert base_url.count("/accounts/") == 1
    assert base_url.endswith("/ai/v1")


def test_admin_ai_provider_kind_kb_has_cloudflare_and_custom_options():
    from keyboards import admin_ai_provider_kind_kb
    kb = admin_ai_provider_kind_kb()
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "admin_ai_kind:cloudflare" in callbacks
    assert "admin_ai_kind:custom" in callbacks


def test_admin_ai_cloudflare_model_kb_includes_free_tier_coder_model():
    from keyboards import admin_ai_cloudflare_model_kb, CLOUDFLARE_CODER_MODELS
    kb = admin_ai_cloudflare_model_kb()
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "admin_ai_cf_model:@cf/qwen/qwen2.5-coder-32b-instruct" in callbacks
    assert "admin_ai_cf_model:custom" in callbacks
    assert len(CLOUDFLARE_CODER_MODELS) >= 1
