from aiogram.fsm.state import State, StatesGroup


class ContactAdmin(StatesGroup):
    waiting_message = State()


class AdminReply(StatesGroup):
    waiting_reply_text = State()


class AddBot(StatesGroup):
    waiting_code = State()
    waiting_requirements = State()
    waiting_build_cmd = State()
    waiting_start_cmd = State()
    waiting_env = State()


class ConfirmDelete(StatesGroup):
    waiting_text = State()


class AdminMessageUser(StatesGroup):
    waiting_text = State()


class StarsTopUp(StatesGroup):
    waiting_amount = State()


class AdminSetLimit(StatesGroup):
    waiting_number = State()


class AdminStarsSetting(StatesGroup):
    waiting_amount = State()
    waiting_hours = State()
    waiting_min_withdraw = State()
    waiting_block_hours = State()
    waiting_ai_help_price = State()


class AdminBanCustomHours(StatesGroup):
    waiting_hours = State()


class AdminBroadcast(StatesGroup):
    waiting_text = State()


class AdminTestDeploy(StatesGroup):
    waiting_token = State()
    waiting_chat_id = State()


class AdminSearchUser(StatesGroup):
    waiting_query = State()


class AdminAIProvider(StatesGroup):
    """Superadmin — AI provider (Cloudflare Workers AI, OpenRouter, va h.k.) qo'shish/tahrirlash."""
    waiting_kind = State()          # "cloudflare" yoki "custom" — qaysi savollar ketma-ketligi ishlatilishini belgilaydi
    waiting_name = State()
    waiting_base_url = State()      # faqat "custom" yo'lida ishlatiladi
    waiting_account_id = State()    # faqat "cloudflare" yo'lida ishlatiladi — URL avtomatik yasaladi
    waiting_api_key = State()
    waiting_model = State()
    waiting_daily_limit = State()
    waiting_price_stars = State()


class FixCode(StatesGroup):
    """Crashed bot uchun 'Kodni almashtirish' — yangi .py fayl kelmaguncha
    eski kod o'chirilmaydi (bot_actions.py'dagi cb_fix_code)."""
    waiting_file = State()


class FixRequirements(StatesGroup):
    """Crashed bot uchun 'requirements.txt almashtirish/tahrirlash'."""
    waiting_file = State()


class FixEnv(StatesGroup):
    """Crashed bot uchun mavjud ENV qiymatlarini tahrirlash (KEY=VALUE, bittadan)."""
    waiting_key_value = State()


class RenameBot(StatesGroup):
    """'🛠 Botni tahrirlash' orqali bot ko'rsatiladigan nomini (display_name) o'zgartirish."""
    waiting_name = State()


class AIChat(StatesGroup):
    """Foydalanuvchi biror tugma/buyruq bilan mos kelmaydigan erkin matn yozganda,
    bot 'AI'ga yozyapsizmi?' deb so'raydi (fallback handler, handlers/ai_chat.py).
    'Ha' bosilsa shu state'ga o'tadi va keyingi xabarlar AI'ga (tanlangan bot
    konteksti bilan) yuboriladi, toki foydalanuvchi biror menyu tugmasini
    bosmaguncha (bu holatda state avtomatik tozalanadi)."""
    choosing_bot = State()   # bir nechta bot bo'lsa, qaysi bot haqida gaplashishni tanlash
    chatting = State()       # tanlangan bot konteksti bilan AI'ga erkin xabar yuborish


class GitHubDeploy(StatesGroup):
    """'🐙 GitHub orqali deploy' — foydalanuvchi public repo linkini yuboradi,
    bot uni ZIP sifatida yuklab, keyin mavjud AddBot.waiting_requirements/
    waiting_build_cmd/waiting_start_cmd/waiting_env oqimiga qo'shiladi (kod
    ikki marta yozilmasin uchun)."""
    waiting_url = State()
