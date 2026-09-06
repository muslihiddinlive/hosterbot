"""
handlers/help_faq.py

Foydalanuvchilar uchun tez-tez so'raladigan savollarga javob beruvchi yordam bo'limi.
Maqsad: adminga "qanday qilaman" degan bir xil savollar bilan murojaat qilishlarini
kamaytirish — javob shu yerda darhol, kutmasdan olinadi.

Ikki yo'l bilan ochiladi: "❓ Yordam" tugmasi yoki /help buyrug'i.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from keyboards import help_topics_kb, help_back_kb

router = Router()

# (topic_id, sarlavha, matn) — matnda HTML formatlash ishlatiladi.
FAQ_TOPICS = {
    "deploy": (
        "🚀 Bot qanday deploy qilinadi?",
        "1️⃣ \"➕ Bot qo'shish\" tugmasini bosing\n"
        "2️⃣ Bot kodingizni <b>.py</b> fayl yoki <b>.zip</b> arxiv sifatida yuboring\n"
        "3️⃣ Agar tashqi kutubxona kerak bo'lsa (masalan <code>aiogram</code>), "
        "<code>requirements.txt</code> yuboring — yo'q bo'lsa \"O'tkazib yuborish\"ni bosing\n"
        "4️⃣ Build buyrug'ini \"✅ Avtomatik\" qilib qoldiring (tavsiya etiladi)\n"
        "5️⃣ Kerak bo'lsa ENV (token va h.k.) qo'shing, keyin \"✅ Tugatish va deploy qilish\"\n\n"
        "Shundan so'ng bot avtomatik build va ishga tushiriladi.",
    ),
    "env": (
        "🔑 ENV / token qayerga kiritiladi?",
        "Bot kodingiz ichida <code>os.environ[\"BOT_TOKEN\"]</code> yoki shunga o'xshash "
        "ENV o'zgaruvchi ishlatsangiz, deploy vaqtida \"➕ Yana ENV qo'shish\" orqali "
        "<code>KEY=VALUE</code> shaklida kiritishingiz kerak.\n\n"
        "Masalan: <code>BOT_TOKEN=123456:AAExxxxx</code>\n\n"
        "Barcha ENV qiymatlar bazada <b>shifrlangan</b> holda saqlanadi.",
    ),
    "crash": (
        "🟡 Botim \"crashed\" (qulagan) bo'lib qoldi, nima qilaman?",
        "1️⃣ \"🤖 Mening botlarim\" → botni tanlang → \"ℹ️ Bot haqida (kod/log/env)\" — "
        "shu yerda oxirgi loglarni ko'rasiz, odatda xato sababi shu yerda yozilgan\n"
        "2️⃣ Eng ko'p uchraydigan sabablar: noto'g'ri token, "
        "<code>ModuleNotFoundError</code> (requirements.txt yetishmagan), yoki kod xatosi\n"
        "3️⃣ Muammoni tuzatib, kerak bo'lsa \"▶️ Ishga tushirish\" tugmasi bilan qayta urinib ko'ring\n\n"
        "Agar tushunolmasangiz, log matnini nusxalab adminga yuboring.",
    ),
    "limit": (
        "📦 Nechta bot deploy qila olaman?",
        "Standart limit — bir nechta bot (adminlar tomonidan belgilangan). "
        "Limitingizni bilish uchun \"➕ Bot qo'shish\"ni bosing — agar limitga yetgan bo'lsangiz, "
        "shu yerda ko'rsatiladi.\n\n"
        "Ko'proq bot kerak bo'lsa, \"📩 Adminga habar berish\" orqali so'rang.",
    ),
    "stars": (
        "💳 Stars orqali to'lov qanday ishlaydi?",
        "Agar admin hali tasdiqlamagan bo'lsa ham, \"💳 Hisob\" bo'limidan Telegram Stars bilan "
        "to'lab, o'zingiz darhol bot host qila olasiz.\n\n"
        "To'langan har bir bot ma'lum vaqt (odatda 24 soat) ishlaydi, muddat tugasa bot "
        "avtomatik to'xtaydi — \"💳 Hisob\" orqali yana uzaytirasiz.",
    ),
    "resources": (
        "💾 RAM/resurs cheklovlari qanday?",
        "Server umumiy resurslari barcha hosted botlar orasida bo'linadi. Har bir botning "
        "hozirgi RAM sarfini \"💾 Resurs\" tugmasi orqali ko'rishingiz mumkin.\n\n"
        "Agar server byudjeti tugagan bo'lsa, yangi bot ishga tushirishdan oldin "
        "boshqa (ishlatilmayotgan) botingizni to'xtatib qo'ying.",
    ),
}


@router.message(Command("help"))
@router.message(F.text == "❓ Yordam")
async def show_help_menu(message: Message):
    await message.answer(
        "❓ <b>Yordam</b>\n\nQuyidagi mavzulardan birini tanlang:",
        parse_mode="HTML",
        reply_markup=help_topics_kb(FAQ_TOPICS),
    )


@router.callback_query(F.data.startswith("help_topic:"))
async def show_help_topic(callback: CallbackQuery):
    topic_id = callback.data.split(":", 1)[1]
    topic = FAQ_TOPICS.get(topic_id)
    if topic is None:
        await callback.answer("Topilmadi.", show_alert=True)
        return
    title, body = topic
    await callback.message.edit_text(
        f"<b>{title}</b>\n\n{body}",
        parse_mode="HTML",
        reply_markup=help_back_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "help_back")
async def back_to_help_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "❓ <b>Yordam</b>\n\nQuyidagi mavzulardan birini tanlang:",
        parse_mode="HTML",
        reply_markup=help_topics_kb(FAQ_TOPICS),
    )
    await callback.answer()
