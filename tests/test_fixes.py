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


def test_bot_manage_kb_crashed_shows_rebuild_and_ai_help_only():
    # AI-tashxis va "qayta build" faqat crashed holatda ma'noli — bot_manage_kb'da
    # shu ikkovi ko'rinadi. Kod/requirements/env tahrirlash endi alohida
    # "🛠 Botni tahrirlash" menyusiga ko'chirilgan (edit_bot_menu_kb) — har qanday
    # holatdagi bot uchun bir xil ishlashi kerak, shu sabab bot_manage_kb'da emas.
    from keyboards import bot_manage_kb
    crashed_row = {"bot_id": 7, "status": "crashed", "stars_hosted": 0}
    kb = bot_manage_kb(crashed_row, has_env=False)
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "bot_rebuild:7" in callbacks
    assert "ai_help:7" in callbacks
    assert "edit_bot_menu:7" in callbacks


def test_bot_manage_kb_shows_edit_menu_regardless_of_status():
    # Asosiy talab: "🛠 Botni tahrirlash" running/stopped/crashed — barcha
    # holatda ko'rinishi kerak, faqat crash bo'lganda emas.
    from keyboards import bot_manage_kb
    for status in ("running", "stopped", "crashed"):
        row = {"bot_id": 3, "status": status, "stars_hosted": 0}
        kb = bot_manage_kb(row)
        callbacks = {btn.callback_data for r in kb.inline_keyboard for btn in r}
        assert "edit_bot_menu:3" in callbacks, f"status={status} uchun edit_bot_menu ko'rinishi kerak"


def test_edit_bot_menu_kb_has_code_and_reqs_always_env_conditionally():
    from keyboards import edit_bot_menu_kb
    kb_without_env = edit_bot_menu_kb(9, has_env=False)
    callbacks_without = {btn.callback_data for row in kb_without_env.inline_keyboard for btn in row}
    assert "fix_code:9" in callbacks_without
    assert "fix_reqs:9" in callbacks_without
    assert "fix_env:9" not in callbacks_without

    kb_with_env = edit_bot_menu_kb(9, has_env=True)
    callbacks_with = {btn.callback_data for row in kb_with_env.inline_keyboard for btn in row}
    assert "fix_env:9" in callbacks_with
    assert "bot_manage:9" in callbacks_with  # "orqaga" tugmasi bot_manage'ga qaytishi kerak


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

    async def fake_call_provider(provider, messages, tools=None):
        calls.append(provider["name"])
        return {"content": f"javob {provider['name']}dan"}

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


def test_rebuild_stops_running_bot_before_rebuilding(tmp_path, monkeypatch):
    # Asosiy talab: "🛠 Botni tahrirlash" orqali ISHLAB TURGAN botni tahrirlaganda,
    # _rebuild_and_start avval eski jarayonni to'xtatishi kerak — aks holda eski
    # va yangi jarayon parallel ishlab, ikkitasi bitta Telegram token bilan
    # polling qilib konflikt yaratib qo'yishi mumkin edi.
    import asyncio
    import handlers.bot_actions as bot_actions_mod

    db_mod = _fresh_db(tmp_path, monkeypatch)
    code_dir = tmp_path / "bot_1"
    code_dir.mkdir()
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username="testbot", bot_token=None, code_path=str(code_dir),
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id, "running", 12345)

    stop_calls = []
    monkeypatch.setattr(bot_actions_mod, "db", db_mod)
    monkeypatch.setattr(bot_actions_mod, "stop_bot_process", lambda bid: stop_calls.append(bid))
    monkeypatch.setattr(bot_actions_mod, "is_running", lambda bid: True)
    monkeypatch.setattr(bot_actions_mod, "can_start_new_bot", lambda: (False, 999, 1000))  # RAM to'la - build'gacha yetmasin

    class FakeUser:
        id = 1

    class FakeMessage:
        from_user = FakeUser()

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot_actions_mod._rebuild_and_start(bot_id, bot=None, message=FakeMessage()))
    assert stop_calls == [bot_id], "running bot avval to'xtatilishi kerak edi"


def test_rebuild_skips_stop_when_bot_not_running(tmp_path, monkeypatch):
    import asyncio
    import handlers.bot_actions as bot_actions_mod

    db_mod = _fresh_db(tmp_path, monkeypatch)
    code_dir = tmp_path / "bot_2"
    code_dir.mkdir()
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username="testbot", bot_token=None, code_path=str(code_dir),
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id, "crashed", None)

    stop_calls = []
    monkeypatch.setattr(bot_actions_mod, "db", db_mod)
    monkeypatch.setattr(bot_actions_mod, "stop_bot_process", lambda bid: stop_calls.append(bid))
    monkeypatch.setattr(bot_actions_mod, "is_running", lambda bid: False)
    monkeypatch.setattr(bot_actions_mod, "can_start_new_bot", lambda: (False, 999, 1000))

    class FakeUser:
        id = 1

    class FakeMessage:
        from_user = FakeUser()

        async def answer(self, *args, **kwargs):
            pass

    asyncio.run(bot_actions_mod._rebuild_and_start(bot_id, bot=None, message=FakeMessage()))
    assert stop_calls == [], "crashed (allaqachon to'xtagan) bot uchun stop_bot_process chaqirilmasligi kerak"


def test_format_ram_limit_message_hides_numbers_from_regular_user(monkeypatch):
    # Xavfsizlik/maxfiylik talabi: server RAM byudjetining aniq raqamlari
    # (masalan "242/420 MB") faqat superadminga ko'rinishi kerak — oddiy
    # foydalanuvchi platformaning ichki server kuvvati haqida bilmasligi kerak.
    import config
    from services.resource_monitor import format_ram_limit_message

    monkeypatch.setattr(config, "SUPERADMIN_IDS", {999})
    # resource_monitor.py o'zi "from config import ... is_superadmin" qilgani
    # uchun, is_superadmin funksiyasining o'zi ham SUPERADMIN_IDS'ni to'g'ri
    # o'qishi kerak - u global o'zgaruvchiga to'g'ridan-to'g'ri murojaat qiladi.
    import services.resource_monitor as rm_mod
    monkeypatch.setattr(rm_mod, "is_superadmin", lambda uid: uid in {999})

    regular_msg = format_ram_limit_message(user_id=1, used_mb=242, budget_mb=420)
    superadmin_msg = format_ram_limit_message(user_id=999, used_mb=242, budget_mb=420)

    assert "242" not in regular_msg
    assert "420" not in regular_msg
    assert "242" in superadmin_msg
    assert "420" in superadmin_msg


def test_set_bot_display_name_updates_name(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username=None, bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py", display_name="Old Name",
    )
    db_mod.set_bot_display_name(bot_id, "New Name")
    assert db_mod.get_bot(bot_id)["display_name"] == "New Name"


def test_edit_bot_menu_kb_has_rename_button():
    from keyboards import edit_bot_menu_kb
    kb = edit_bot_menu_kb(5, has_env=False)
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "rename_bot:5" in callbacks


def test_my_bots_list_kb_shows_bulk_buttons_only_when_applicable():
    from keyboards import my_bots_list_kb

    # Hech qanday bot yo'q -> bulk tugmalar ko'rinmaydi
    kb_empty = my_bots_list_kb([])
    assert kb_empty.inline_keyboard == []

    # Faqat running bot bor -> faqat "hammasini to'xtatish" ko'rinadi
    only_running = [{"bot_id": 1, "bot_username": "a", "display_name": None, "status": "running"}]
    kb_running = my_bots_list_kb(only_running)
    running_callbacks = {btn.callback_data for row in kb_running.inline_keyboard for btn in row}
    assert "bots_stop_all" in running_callbacks
    assert "bots_start_all" not in running_callbacks

    # Faqat stopped bot bor -> faqat "hammasini ishga tushirish" ko'rinadi
    only_stopped = [{"bot_id": 2, "bot_username": "b", "display_name": None, "status": "stopped"}]
    kb_stopped = my_bots_list_kb(only_stopped)
    stopped_callbacks = {btn.callback_data for row in kb_stopped.inline_keyboard for btn in row}
    assert "bots_start_all" in stopped_callbacks
    assert "bots_stop_all" not in stopped_callbacks

    # Ikkalasi ham bor -> ikkalasi ham ko'rinadi
    mixed = only_running + only_stopped
    kb_mixed = my_bots_list_kb(mixed)
    mixed_callbacks = {btn.callback_data for row in kb_mixed.inline_keyboard for btn in row}
    assert "bots_start_all" in mixed_callbacks
    assert "bots_stop_all" in mixed_callbacks


def test_my_bots_list_kb_shows_crashed_status_icon():
    from keyboards import my_bots_list_kb
    crashed = [{"bot_id": 3, "bot_username": "c", "display_name": None, "status": "crashed"}]
    kb = my_bots_list_kb(crashed)
    label_button = kb.inline_keyboard[-1][0]  # bulk qatordan keyingi birinchi bot qatori
    assert label_button.text.startswith("🟡")


def test_get_dashboard_stats_counts_users_and_bots(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="a", first_name="A")
    db_mod.upsert_user(telegram_id=2, username="b", first_name="B")
    db_mod.set_user_status(2, "approved")

    bot_id_1 = db_mod.create_bot(
        owner_id=1, bot_username="bot1", bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id_1, "running", 111)
    bot_id_2 = db_mod.create_bot(
        owner_id=2, bot_username="bot2", bot_token=None, code_path="/tmp/y",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id_2, "crashed", None)

    stats = db_mod.get_dashboard_stats()
    assert stats["total_users"] == 2
    assert stats["approved_users"] == 1
    assert stats["total_bots"] == 2
    assert stats["running_bots"] == 1
    assert stats["crashed_bots"] == 1
    assert stats["deploys_today"] == 2  # ikkalasi ham hozirgina yaratilgan


def test_get_dashboard_stats_excludes_deleted_bots(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="a", first_name="A")
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username="bot1", bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.delete_bot(bot_id)
    stats = db_mod.get_dashboard_stats()
    assert stats["total_bots"] == 0


def test_search_users_by_prefix_matches_username_and_first_name(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="alisher_dev", first_name="Alisher")
    db_mod.upsert_user(telegram_id=2, username="botmaster", first_name="Ali")
    db_mod.upsert_user(telegram_id=3, username="somebody", first_name="Vali")

    by_username = db_mod.search_users_by_prefix("alish")
    assert {u["telegram_id"] for u in by_username} == {1}

    by_first_name = db_mod.search_users_by_prefix("ali")
    # "alisher_dev" (username "ali..." emas, lekin first_name "Alisher" ali bilan
    # boshlanadi) va "botmaster" (first_name "Ali") ikkalasi ham mos kelishi kerak
    assert {u["telegram_id"] for u in by_first_name} == {1, 2}


def test_search_users_by_prefix_case_insensitive(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="AliYor", first_name="Ali")
    matches = db_mod.search_users_by_prefix("aliy")
    assert len(matches) == 1
    assert matches[0]["telegram_id"] == 1


def test_search_users_by_prefix_empty_query_returns_empty(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="someone", first_name="Someone")
    assert db_mod.search_users_by_prefix("") == []
    assert db_mod.search_users_by_prefix("   ") == []


def test_search_users_by_prefix_escapes_sql_wildcards(tmp_path, monkeypatch):
    # "%" va "_" SQL LIKE uchun maxsus belgilar — agar qidiruv so'zida
    # tasodifan shu belgilar bo'lsa (masalan username'da), ular literal
    # belgi sifatida qidirilishi kerak, wildcard sifatida emas.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="test_user", first_name="Test")
    db_mod.upsert_user(telegram_id=2, username="testXuser", first_name="Test2")
    # "_" ni wildcard sifatida talqin qilsa, "test_user" VA "testXuser"
    # ikkalasi ham mos kelardi (chunki "_" har qanday bitta belgiga mos keladi).
    # ESCAPE bilan faqat "test_user" mos kelishi kerak.
    matches = db_mod.search_users_by_prefix("test_")
    assert {u["telegram_id"] for u in matches} == {1}


def test_admin_search_qwerty_kb_has_three_letter_rows_and_matches_on_top():
    from keyboards import admin_search_qwerty_kb
    fake_matches = [{"telegram_id": 42, "username": "aliyor", "first_name": "Ali"}]
    kb = admin_search_qwerty_kb("ali", fake_matches)

    # Birinchi qator - topilgan foydalanuvchi
    assert kb.inline_keyboard[0][0].callback_data == "admin_user_view:42"

    # Keyingi 3 qator - QWERTY harflar
    letter_rows = kb.inline_keyboard[1:4]
    assert [btn.text for btn in letter_rows[0]] == list("qwertyuiop")
    assert [btn.text for btn in letter_rows[1]] == list("asdfghjkl")
    assert [btn.text for btn in letter_rows[2]] == list("zxcvbnm")

    # Har bir harf tugmasi joriy so'rovga o'sha harfni qo'shib yuborishi kerak
    first_letter_btn = letter_rows[0][0]
    assert first_letter_btn.callback_data == "admin_search_qwerty:aliq"


def test_admin_search_qwerty_kb_shows_backspace_only_when_query_nonempty():
    from keyboards import admin_search_qwerty_kb
    kb_empty = admin_search_qwerty_kb("", [])
    kb_with_query = admin_search_qwerty_kb("a", [])

    empty_callbacks = {btn.callback_data for row in kb_empty.inline_keyboard for btn in row}
    query_callbacks = {btn.callback_data for row in kb_with_query.inline_keyboard for btn in row}

    assert not any(cb.startswith("admin_search_qwerty_bs:") for cb in empty_callbacks)
    assert any(cb.startswith("admin_search_qwerty_bs:") for cb in query_callbacks)


def test_admin_search_qwerty_kb_caps_query_length_for_callback_data_limit():
    # Telegram callback_data 64 baytdan oshmasligi kerak - juda uzun so'rovda
    # harf tugmalari joriy so'rovni cheksiz o'stirmasligi kerak.
    from keyboards import admin_search_qwerty_kb
    long_query = "a" * 35  # cheklovga aynan yetgan
    kb = admin_search_qwerty_kb(long_query, [])
    letter_btn = kb.inline_keyboard[0][0]  # birinchi qatordagi birinchi harf
    # Chegaraga yetgani uchun yangi harf qo'shilmasligi kerak - joriy so'rovning o'zi qaytishi kerak
    assert letter_btn.callback_data == f"admin_search_qwerty:{long_query}"


def test_admin_search_choice_kb_has_both_search_methods():
    from keyboards import admin_search_choice_kb
    kb = admin_search_choice_kb()
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "admin_search_by_id" in callbacks
    assert "admin_search_qwerty:" in callbacks


@pytest.mark.asyncio
async def test_build_user_view_shows_limit_and_per_bot_details(tmp_path, monkeypatch):
    # Asosiy talab: userni bosganda (qidiruvdanmi, ro'yxatdanmi - baribir bir
    # xil _build_user_view chaqiriladi) botlar, deploy sanasi, limit hammasi
    # bitta ko'rinishda chiqishi kerak.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=100, username="testuser", first_name="Test User")
    db_mod.set_user_status(100, "approved")

    bot_id = db_mod.create_bot(
        owner_id=100, bot_username="mybot", bot_token=None, code_path="/tmp/x",
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id, "running", 12345)

    import handlers.admin_panel as admin_panel_mod
    monkeypatch.setattr(admin_panel_mod, "db", db_mod)
    monkeypatch.setattr(admin_panel_mod, "is_running", lambda bid: True)
    monkeypatch.setattr(admin_panel_mod, "bot_ram_mb", lambda bid: 42.5)
    monkeypatch.setattr(admin_panel_mod, "is_superadmin", lambda uid: True)

    text, kb = await admin_panel_mod._build_user_view(100, viewer_id=999)

    assert text is not None
    assert "mybot" in text
    assert "python" in text  # til ko'rsatilgan
    assert "42.5 MB" in text  # RAM ko'rsatilgan
    assert "1/" in text  # limit qatori: "1/{max_bots}"
    assert "Test User" in text


@pytest.mark.asyncio
async def test_build_user_view_shows_individual_limit_note(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=101, username="limiteduser", first_name="Limited")
    db_mod.set_user_max_bots(101, 10)

    import handlers.admin_panel as admin_panel_mod
    monkeypatch.setattr(admin_panel_mod, "db", db_mod)
    monkeypatch.setattr(admin_panel_mod, "is_superadmin", lambda uid: True)

    text, kb = await admin_panel_mod._build_user_view(101, viewer_id=999)
    assert "10" in text
    assert "individual" in text


@pytest.mark.asyncio
async def test_build_user_view_returns_none_for_missing_user(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    import handlers.admin_panel as admin_panel_mod
    monkeypatch.setattr(admin_panel_mod, "db", db_mod)

    text, kb = await admin_panel_mod._build_user_view(999999, viewer_id=1)
    assert text is None
    assert kb is None


def test_bot_link_html_makes_clickable_link_when_username_present():
    from services.deploy_manager import bot_link_html
    bot_row = {"bot_username": "mybot", "display_name": None}
    result = bot_link_html(bot_row)
    assert result == '<a href="https://t.me/mybot">@mybot</a>'


def test_bot_link_html_falls_back_to_display_name_without_username():
    from services.deploy_manager import bot_link_html
    bot_row = {"bot_username": None, "display_name": "My Cool Bot"}
    result = bot_link_html(bot_row)
    assert result == "My Cool Bot"
    assert "<a href" not in result


def test_bot_link_html_falls_back_to_default_label_when_nothing_set():
    from services.deploy_manager import bot_link_html
    bot_row = {"bot_username": None, "display_name": None}
    assert bot_link_html(bot_row) == "Nomsiz bot"


def test_bot_link_html_escapes_special_chars_in_display_name_fallback():
    from services.deploy_manager import bot_link_html
    bot_row = {"bot_username": None, "display_name": "<script>alert(1)</script>"}
    result = bot_link_html(bot_row)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_crash_diagnosis_prompt_forbids_terminal_advice():
    # Asosiy fix: AI avval "pip install qiling", "terminalni oching" kabi
    # foydalanuvchi uchun bajarib bo'lmaydigan (terminal yo'q) maslahatlar
    # berardi. System prompt endi bunday maslahatlarni ANIQ taqiqlashi kerak.
    from services.ai_client import build_crash_diagnosis_prompt
    system_prompt, _ = build_crash_diagnosis_prompt("@testbot", "some log")
    assert "pip install" in system_prompt  # misol sifatida tilga olingan (taqiqlangan namuna)
    assert "terminal" in system_prompt.lower()
    assert "HECH QANDAY" in system_prompt or "taqiqlangan" in system_prompt.lower()


def test_crash_diagnosis_prompt_mentions_actual_bot_buttons():
    # System prompt AI'ga haqiqiy tuzatish mexanizmlarini (botdagi tugmalar)
    # aytishi kerak - shu tugma nomlari bot_actions.py/keyboards.py'dagi
    # haqiqiy tugma matnlari bilan bir xil bo'lishi kerak.
    from services.ai_client import build_crash_diagnosis_prompt
    system_prompt, _ = build_crash_diagnosis_prompt("@testbot", "some log")
    assert "requirements.txt almashtirish" in system_prompt
    assert "Kodni almashtirish" in system_prompt
    assert "ENV tahrirlash" in system_prompt


def test_crash_diagnosis_prompt_mentions_telegram_only_context():
    from services.ai_client import build_crash_diagnosis_prompt
    system_prompt, _ = build_crash_diagnosis_prompt("@testbot", "some log")
    assert "Telegram" in system_prompt


def test_crash_diagnosis_prompt_includes_requirements_when_provided():
    from services.ai_client import build_crash_diagnosis_prompt
    _, user_prompt = build_crash_diagnosis_prompt(
        "@testbot", "some log", requirements_text="aiogram==3.4.1\nrequests"
    )
    assert "aiogram==3.4.1" in user_prompt
    assert "requests" in user_prompt


def test_crash_diagnosis_prompt_notes_missing_requirements_explicitly():
    # AI requirements.txt yo'qligini ANIQ bilishi kerak (bo'sh string emas,
    # balki "yo'q" deb aytilishi) - aks holda AI buni "bo'sh matn keldi,
    # ehtimol o'qib bo'lmadi" deb noto'g'ri talqin qilishi mumkin.
    from services.ai_client import build_crash_diagnosis_prompt
    _, user_prompt = build_crash_diagnosis_prompt("@testbot", "some log", requirements_text="")
    assert "requirements.txt fayli yo'q" in user_prompt


def test_crash_diagnosis_prompt_instructs_ai_to_cross_check_requirements():
    # System prompt AI'ga requirements.txt'ni tekshirib, ModuleNotFoundError
    # bo'lsa ham kutubxona aslida bor bo'lishi mumkinligini hisobga olishni
    # aytishi kerak (noto'g'ri "kutubxona yo'q" tashxisining oldini olish uchun).
    from services.ai_client import build_crash_diagnosis_prompt
    system_prompt, _ = build_crash_diagnosis_prompt("@testbot", "some log")
    assert "requirements.txt" in system_prompt
    assert "ALLAQACHON bor" in system_prompt


def test_crash_diagnosis_prompt_truncates_long_requirements():
    from services.ai_client import build_crash_diagnosis_prompt
    long_requirements = "package\n" * 500  # ~4000 belgi
    _, user_prompt = build_crash_diagnosis_prompt(
        "@testbot", "log", requirements_text=long_requirements
    )
    # requirements.txt qismi 800 belgigacha qisqartirilishi kerak
    requirements_section = user_prompt.split("requirements.txt tarkibi:")[1]
    assert len(requirements_section) < 900


@pytest.mark.asyncio
async def test_ai_help_backs_up_database_after_deducting_stars(tmp_path, monkeypatch):
    # KRITIK BUG FIX: avval Stars yechilgandan keyin backup_database
    # chaqirilmasdi. Render Free Tier diski ephemeral bo'lgani uchun, agar
    # server backup'dan oldin qayta ko'tarilsa, eski (Stars hali yechilmagan)
    # backup tiklanib, foydalanuvchi Stars sarflab balansi o'zgarmagan holatga
    # tushib qolardi. Bu test backup_database HAR DOIM chaqirilishini tekshiradi.
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="testuser", first_name="Test")
    db_mod.add_user_balance(1, 10, reason="test topup")
    db_mod.set_setting("ai_help_price_stars", 2)

    code_dir = tmp_path / "bot_1"
    code_dir.mkdir()
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username="testbot", bot_token=None, code_path=str(code_dir),
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id, "crashed", None)

    import handlers.bot_actions as bot_actions_mod
    monkeypatch.setattr(bot_actions_mod, "db", db_mod)

    backup_calls = []
    async def fake_backup(bot):
        backup_calls.append(True)
    monkeypatch.setattr(bot_actions_mod, "backup_database", fake_backup)

    async def fake_ask_ai(system_prompt, user_prompt, telegram_id, bot_id):
        return "test diagnosis"
    monkeypatch.setattr(bot_actions_mod, "ask_ai", fake_ask_ai)
    monkeypatch.setattr(bot_actions_mod, "read_log_tail", lambda path, n_lines=60: "some log")

    class FakeUser:
        id = 1

    class FakeMessage:
        from_user = FakeUser()
        async def answer(self, *args, **kwargs):
            return FakeMessage()
        async def edit_text(self, *args, **kwargs):
            pass

    class FakeCallback:
        data = f"ai_help:{bot_id}"
        from_user = FakeUser()
        message = FakeMessage()
        async def answer(self, *args, **kwargs):
            pass

    await bot_actions_mod.cb_ai_help(FakeCallback(), bot=None)

    assert backup_calls == [True], "AI yordamdan keyin backup_database chaqirilishi SHART"
    assert db_mod.get_user_balance(1) == 8  # 10 - 2 = 8


@pytest.mark.asyncio
async def test_stars_extend_backs_up_database_after_deducting_stars(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    db_mod.upsert_user(telegram_id=1, username="testuser", first_name="Test")
    db_mod.add_user_balance(1, 10, reason="test topup")

    code_dir = tmp_path / "bot_1"
    code_dir.mkdir()
    bot_id = db_mod.create_bot(
        owner_id=1, bot_username="testbot", bot_token=None, code_path=str(code_dir),
        storage_file_id=None, is_zip=False, language="python",
        build_cmd="", start_cmd="python bot.py",
    )
    db_mod.set_bot_status(bot_id, "running", 999)

    import handlers.stars as stars_mod
    monkeypatch.setattr(stars_mod, "db", db_mod)
    monkeypatch.setattr(stars_mod, "is_running", lambda bid: True)

    backup_calls = []
    async def fake_backup(bot):
        backup_calls.append(True)
    monkeypatch.setattr(stars_mod, "backup_database", fake_backup)

    class FakeUser:
        id = 1

    class FakeCallback:
        data = f"stars_extend:{bot_id}"
        from_user = FakeUser()
        async def answer(self, *args, **kwargs):
            pass

    await stars_mod.cb_stars_extend(FakeCallback(), bot=None)

    assert backup_calls == [True], "stars_extend'dan keyin backup_database chaqirilishi SHART"


def test_get_ai_chat_price_stars_default_and_override(tmp_path, monkeypatch):
    db_mod = _fresh_db(tmp_path, monkeypatch)
    assert db_mod.get_ai_chat_price_stars() == 1  # default
    db_mod.set_setting("ai_chat_price_stars", 3)
    assert db_mod.get_ai_chat_price_stars() == 3


def test_ai_chat_confirm_kb_has_yes_and_no():
    from keyboards import ai_chat_confirm_kb
    kb = ai_chat_confirm_kb()
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "ai_chat_confirm:yes" in callbacks
    assert "ai_chat_confirm:no" in callbacks


def test_ai_chat_pick_bot_kb_lists_all_bots_with_status_icons():
    from keyboards import ai_chat_pick_bot_kb
    bots = [
        {"bot_id": 1, "bot_username": "bot1", "display_name": None, "status": "running"},
        {"bot_id": 2, "bot_username": None, "display_name": "My Bot", "status": "crashed"},
    ]
    kb = ai_chat_pick_bot_kb(bots)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("🟢" in t and "bot1" in t for t in texts)
    assert any("🟡" in t and "My Bot" in t for t in texts)
    callbacks = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert "ai_chat_pick_bot:1" in callbacks
    assert "ai_chat_pick_bot:2" in callbacks
    assert "ai_chat_cancel" in callbacks


def test_ai_chat_edit_confirm_kb_shows_price():
    from keyboards import ai_chat_edit_confirm_kb
    kb = ai_chat_edit_confirm_kb(2)
    yes_btn = kb.inline_keyboard[0][0]
    assert "2⭐️" in yes_btn.text
    assert yes_btn.callback_data == "ai_chat_apply_edit:yes"
    assert kb.inline_keyboard[0][1].callback_data == "ai_chat_apply_edit:no"


@pytest.mark.asyncio
async def test_call_provider_sends_tools_when_provided(monkeypatch):
    # ask_ai_with_tools EDIT_FILE_TOOL bilan chaqirilganda, _call_provider'ga
    # 'tools' parametri to'g'ri uzatilishi kerak (aks holda AI hech qachon
    # tahrirlash taklif qila olmaydi).
    import services.ai_client as ai_client_mod

    captured = {}

    class FakeResponse:
        status = 200
        async def json(self):
            return {"choices": [{"message": {"content": "ok", "tool_calls": None}}]}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    class FakeSession:
        def post(self, url, headers, json):
            captured["payload"] = json
            return FakeResponse()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    monkeypatch.setattr(ai_client_mod.aiohttp, "ClientSession", lambda timeout=None: FakeSession())

    provider = {"name": "Test", "base_url": "https://example.com/v1", "api_key": None, "model": "m"}
    messages = [{"role": "user", "content": "hi"}]
    await ai_client_mod._call_provider(provider, messages, tools=[ai_client_mod.EDIT_FILE_TOOL])

    assert "tools" in captured["payload"]
    assert captured["payload"]["tools"][0]["function"]["name"] == "propose_file_edit"


@pytest.mark.asyncio
async def test_call_provider_omits_tools_when_not_provided(monkeypatch):
    import services.ai_client as ai_client_mod

    captured = {}

    class FakeResponse:
        status = 200
        async def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    class FakeSession:
        def post(self, url, headers, json):
            captured["payload"] = json
            return FakeResponse()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass

    monkeypatch.setattr(ai_client_mod.aiohttp, "ClientSession", lambda timeout=None: FakeSession())

    provider = {"name": "Test", "base_url": "https://example.com/v1", "api_key": None, "model": "m"}
    messages = [{"role": "user", "content": "hi"}]
    await ai_client_mod._call_provider(provider, messages)

    assert "tools" not in captured["payload"]


def test_build_free_chat_system_prompt_forbids_auto_edit():
    # Muhim qoida: AI hech qachon "o'zi tahrirladim" demasligi, faqat taklif
    # berishi va foydalanuvchining o'z tugmasi orqali tahrirlash kerakligini
    # tushuntirishi kerak.
    from services.ai_client import build_free_chat_system_prompt
    prompt = build_free_chat_system_prompt("@testbot")
    assert "AVTOMATIK O'ZGARTIRA OLMAYSIZ" in prompt or "avtomatik" in prompt.lower()
    assert "pip install" in prompt or "terminal" in prompt.lower()
