"""
services/ai_client.py

Muammo: bitta AI provayder (masalan Cloudflare Workers AI) kunlik neuron/so'rov
byudjeti bilan cheklangan — agar barcha foydalanuvchilar shu bitta hisobdan
foydalansa, byudjet tez tugaydi va AI yordam ishlamay qoladi.

Yechim: superadmin bir nechta AI provayder (istalgan OpenAI-compatible endpoint —
Cloudflare Workers AI, OpenRouter, Groq va h.k.) qo'sha oladi, har biriga alohida
kunlik so'rov limiti va ustuvorlik (priority) belgilaydi. Bu modul ustuvorlik
tartibida provayderlarni birma-bir sinab ko'radi: birinchisi kunlik limitga
yetgan yoki xato qaytarsa, avtomatik keyingisiga o'tadi (fallback).

DIQQAT: bu yerda "limit" — bizning tomonimizdan kuzatiladigan so'rov SONI
(ai_usage_log jadvali), token/neuron sarfi emas — chunki har xil provayderning
token->neuron nisbati boshqacha, va superadmin o'zi qancha so'rov xavfsiz
ekanini biladi (Cloudflare dashboard'idagi haqiqiy Neuron sarfiga qarab).
"""
import logging

import aiohttp

import database as db

log = logging.getLogger("hosterbot.ai_client")

REQUEST_TIMEOUT_SECONDS = 30


class AIError(Exception):
    """Barcha faol provayderlar sinab ko'rilgandan keyin ham javob olinmasa."""


async def _call_provider(provider: dict, messages: list, tools: list = None) -> dict:
    """Bitta provayderga OpenAI-compatible /v1/chat/completions so'rovi yuboradi.
    Cloudflare Workers AI ham shu formatni to'liq qo'llab-quvvatlaydi
    (developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/).

    'messages' — to'liq OpenAI-uslubdagi xabarlar ro'yxati (system/user/assistant/tool).
    'tools' — agar berilsa (AI chat rejimida function-calling uchun), OpenAI-uslubdagi
    tool-schema ro'yxati yuboriladi. Qaytaradi: to'liq 'message' obyekti (dict) —
    chaqiruvchi tomon 'content' (matn javob) yoki 'tool_calls' (AI tahrirlash
    taklif qilgan holat) borligini o'zi tekshiradi."""
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    payload = {
        "model": provider["model"],
        "messages": messages,
        "max_tokens": 800,
    }
    if tools:
        payload["tools"] = tools

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise AIError(f"{provider['name']}: HTTP {resp.status} — {body[:300]}")
            data = await resp.json()

    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"{provider['name']}: kutilmagan javob formati — {e}")


async def ask_ai(system_prompt: str, user_prompt: str, telegram_id: int, bot_id: int = None) -> str:
    """Ustuvorlik tartibida faol provayderlarni birma-bir sinab ko'radi.
    Har bir muvaffaqiyatli/muvaffaqiyatsiz urinish ai_usage_log'ga yoziladi
    (kunlik limit hisoblash va admin uchun tarix uchun).

    Qaytaradi: AI javobi (matn).
    Ko'taradi: AIError — hech bir provayder ishlamasa yoki faol provayder umuman yo'q bo'lsa.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    providers = db.list_ai_providers(active_only=True)
    if not providers:
        raise AIError("Hozircha faol AI provayder sozlanmagan — superadmin bilan bog'laning.")

    last_error = None
    for provider in providers:
        if provider["daily_limit"] > 0:
            used_today = db.get_ai_provider_usage_today(provider["provider_id"])
            if used_today >= provider["daily_limit"]:
                log.info(f"AI provider '{provider['name']}' kunlik limitga yetdi ({used_today}/{provider['daily_limit']}), keyingisiga o'tilmoqda")
                continue

        try:
            message = await _call_provider(provider, messages)
            db.log_ai_usage(provider["provider_id"], telegram_id, bot_id, success=True)
            return (message.get("content") or "").strip()
        except Exception as e:
            log.warning(f"AI provider '{provider['name']}' xato qaytardi: {e}")
            db.log_ai_usage(provider["provider_id"], telegram_id, bot_id, success=False)
            last_error = e
            continue

    if last_error:
        raise AIError(f"Barcha AI provayderlar band yoki xato qaytardi. Oxirgi xato: {last_error}")
    raise AIError("Barcha AI provayderlar bugungi kunlik limitga yetgan — ertaga qayta urinib ko'ring.")


# ---------- Erkin AI suhbat rejimi uchun function-calling (kod/requirements tahrirlash taklifi) ----------

EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_file_edit",
        "description": (
            "Botning kod fayli (.py) yoki requirements.txt faylini tahrirlashni TAKLIF QILISH "
            "uchun ishlatiladi. Bu funksiya faylni O'ZI o'zgartirmaydi — faqat foydalanuvchiga "
            "ko'rsatiladigan taklif tayyorlaydi, foydalanuvchi tasdiqlagandan keyingina haqiqiy "
            "fayl o'zgaradi. Faqat foydalanuvchi aniq tuzatish/o'zgartirish so'raganda yoki siz "
            "aniq bir xatoni tuzatish kerakligini aniqlab, buni taklif qilmoqchi bo'lganingizda "
            "chaqiring — shunchaki tushuntirish so'ralganda chaqirmang."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["code", "requirements"],
                    "description": "Qaysi faylni o'zgartirish taklif qilinyapti: 'code' (.py fayl) yoki 'requirements' (requirements.txt)",
                },
                "new_content": {
                    "type": "string",
                    "description": "Faylning TO'LIQ yangi tarkibi (faqat o'zgargan qism emas — butun fayl matni)",
                },
                "explanation": {
                    "type": "string",
                    "description": "Nima o'zgartirilganini va nima uchun kerakligini qisqa, oddiy o'zbek tilida tushuntirish",
                },
            },
            "required": ["target", "new_content", "explanation"],
        },
    },
}


async def ask_ai_with_tools(messages: list, telegram_id: int, bot_id: int = None) -> dict:
    """Erkin AI suhbat rejimi uchun — ask_ai bilan bir xil provider-fallback
    mantig'i, lekin EDIT_FILE_TOOL bilan chaqiriladi va TO'LIQ message obyektini
    qaytaradi (content YOKI tool_calls borligini chaqiruvchi tomon o'zi tekshiradi).

    DIQQAT: har bir model tool-calling'ni qo'llab-quvvatlamasligi mumkin — agar
    provayder tool-calling formatini tushunmasa, oddiy 'content' javob qaytarishi
    kutiladi (bu holatda AI shunchaki tahrirlash taklif qila olmaydi, faqat
    matn bilan tushuntiradi — funksionallik yo'qolmaydi, faqat kengaytirilgan
    imkoniyat ishlamaydi)."""
    providers = db.list_ai_providers(active_only=True)
    if not providers:
        raise AIError("Hozircha faol AI provayder sozlanmagan — superadmin bilan bog'laning.")

    last_error = None
    for provider in providers:
        if provider["daily_limit"] > 0:
            used_today = db.get_ai_provider_usage_today(provider["provider_id"])
            if used_today >= provider["daily_limit"]:
                log.info(f"AI provider '{provider['name']}' kunlik limitga yetdi ({used_today}/{provider['daily_limit']}), keyingisiga o'tilmoqda")
                continue

        try:
            message = await _call_provider(provider, messages, tools=[EDIT_FILE_TOOL])
            db.log_ai_usage(provider["provider_id"], telegram_id, bot_id, success=True)
            return message
        except Exception as e:
            log.warning(f"AI provider '{provider['name']}' xato qaytardi: {e}")
            db.log_ai_usage(provider["provider_id"], telegram_id, bot_id, success=False)
            last_error = e
            continue

    if last_error:
        raise AIError(f"Barcha AI provayderlar band yoki xato qaytardi. Oxirgi xato: {last_error}")
    raise AIError("Barcha AI provayderlar bugungi kunlik limitga yetgan — ertaga qayta urinib ko'ring.")


def build_crash_diagnosis_prompt(bot_label: str, log_text: str, code_snippet: str = "",
                                  requirements_text: str = "") -> tuple[str, str]:
    """Crash tashxis so'rovi uchun system+user promptlarni tayyorlaydi.
    Log matni oxirgi ~2000 belgigacha qisqartiriladi (token sarfini kamaytirish uchun) —
    odatda xato haqidagi eng muhim ma'lumot (traceback) log oxirida bo'ladi.

    DIQQAT: AI dastlab "sizning kompyuteringizda pip install qiling" kabi
    umumiy dasturchi maslahatlari berardi — bu FOYDALANUVCHIGA MA'NOSIZ, chunki
    u faqat Telegram orqali ishlaydi, hech qanday terminal/kompyuter kirish
    huquqi yo'q. System prompt shu sababli platformaning haqiqiy muhitini va
    haqiqiy tuzatish mexanizmlarini (botdagi tugmalar) aniq tushuntiradi.

    requirements_text — botning requirements.txt tarkibi (agar bo'lsa). Buni
    bermasak, AI "kutubxona yetishmayapti" desa ham, u aslida requirements.txt'da
    bor-yo'qligini bilmasdan taxmin qilardi (masalan versiya nomuvofiqligi yoki
    noto'g'ri yozilgan nom sabab bo'lgan holatlarda noto'g'ri tashxis qo'yishi
    mumkin edi)."""
    system_prompt = (
        "Siz 'HosterBot' nomli Telegram-orqali-boshqariladigan bot hosting platformasidagi "
        "yordamchi diagnostsiz. MUHIM KONTEKST: foydalanuvchi kodini FAQAT Telegram orqali "
        "yuklaydi va bot serverda (bulutda) avtomatik ishga tushiriladi — foydalanuvchida "
        "HECH QANDAY terminal, komanda qatori yoki kompyuteriga kirish huquqi yo'q. Shu sabab:\n\n"
        "QAT'IY TAQIQLANGAN maslahatlar (bularni HECH QACHON yozmang, chunki ular "
        "foydalanuvchi uchun bajarib bo'lmaydi): 'terminalni oching', "
        "'pip install ... buyrug'ini yozing', 'python fayl.py bilan ishga tushiring', "
        "'virtual muhitni faollashtiring (venv/source activate)', "
        "'papkaga o'ting (cd)', yoki boshqa har qanday komanda-qatoriga oid ko'rsatma.\n\n"
        "Buning o'rniga, xato turiga qarab FAQAT quyidagi Telegram bot tugmalaridan mos "
        "kelganini tavsiya qiling (bot interfeysida bot boshqaruv sahifasida mavjud):\n"
        "- ModuleNotFoundError / kutubxona yetishmasa -> '📋 requirements.txt almashtirish' "
        "tugmasi orqali yetishmagan kutubxona nomini requirements.txt fayliga qo'shib qayta yuborish\n"
        "- Kod ichida xato (SyntaxError, mantiqiy xato, noto'g'ri yozilgan qism) -> "
        "'📄 Kodni almashtirish' tugmasi orqali tuzatilgan .py faylni qayta yuborish\n"
        "- Token/API kalit/ENV qiymati noto'g'ri yoki yo'q -> '🔑 ENV tahrirlash' tugmasi\n"
        "- Fayl formatidagi xato (masalan .py.txt) -> to'g'ri kengaytmali faylni qayta yuborish\n\n"
        "Sizga log, kod va (bo'lsa) requirements.txt beriladi — requirements.txt'ni albatta "
        "tekshiring: agar xato 'ModuleNotFoundError' bo'lsa-yu, o'sha kutubxona requirements.txt'da "
        "ALLAQACHON bor bo'lsa, muammo kutubxona yo'qligida emas — balki nom xato yozilgan, versiya "
        "nomuvofiqligi, yoki requirements.txt umuman ishlatilmagan bo'lishi mumkin, shunga qarab "
        "tashxis bering.\n\n"
        "Vazifangiz: xato sababini ODDIY, tushunarli o'zbek tilida tushuntirish va yuqoridagi "
        "ro'yxatdan ANIQ qaysi tugmani bosish kerakligini aytish. Texnik jargon ishlatmang, "
        "oddiy foydalanuvchi (dasturchi bo'lmasligi mumkin) tushunadigan qilib yozing. "
        "Javobni 200-300 so'zdan oshirmang."
    )
    truncated_log = log_text[-2000:] if len(log_text) > 2000 else log_text
    user_prompt = f"Bot: {bot_label}\n\nOxirgi log:\n{truncated_log}"
    if requirements_text:
        user_prompt += f"\n\nrequirements.txt tarkibi:\n{requirements_text[:800]}"
    else:
        user_prompt += "\n\nrequirements.txt: bu botda requirements.txt fayli yo'q (yoki bo'sh)."
    if code_snippet:
        user_prompt += f"\n\nKod parchasi (agar tegishli bo'lsa):\n{code_snippet[:1500]}"
    return system_prompt, user_prompt


def build_free_chat_system_prompt(bot_label: str) -> str:
    """Erkin AI suhbat rejimi (fallback handler orqali, foydalanuvchi tugma/buyruq
    bilan mos kelmaydigan matn yozib, 'Ha, AI'ga yozyapman' desa) uchun system prompt.

    Crash-tashxisdan farqi: bu yerda foydalanuvchi ixtiyoriy savol berishi mumkin
    (nafaqat crash haqida) — kod tushunish, requirements.txt haqida savol,
    umumiy dasturlash savoli va h.k. MUHIM QOIDA: AI kodni/requirements'ni O'QIY
    va TUSHUNTIRA oladi, lekin HECH QACHON o'zi avtomatik tahrirlamaydi — har
    qanday o'zgarish faqat foydalanuvchining o'z xohishi bilan, botdagi tegishli
    tugma (Kodni almashtirish / requirements.txt almashtirish) orqali amalga oshadi."""
    return (
        "Siz 'HosterBot' nomli Telegram-orqali-boshqariladigan bot hosting platformasidagi "
        "yordamchi AI-suhbatdoshsiz. Foydalanuvchi hozir sizga '"
        f"{bot_label}' nomli boti haqida erkin savol-javob rejimida yozmoqda (crash bo'lgani "
        "uchun emas, umuman savol berish uchun).\n\n"
        "MUHIM KONTEKST: foydalanuvchida HECH QANDAY terminal yoki kompyuterga kirish huquqi "
        "yo'q — 'pip install', 'terminalni oching' kabi komanda-qatoriga oid maslahatlarni "
        "HECH QACHON bermang.\n\n"
        "Sizga botning kodi va requirements.txt (agar bo'lsa) beriladi. Siz ularni O'QIY va "
        "TUSHUNTIRA olasiz — nima qilishini, qanday ishlashini, nima uchun xato bo'lishi "
        "mumkinligini tushuntirishingiz mumkin. Agar foydalanuvchi tuzatish yoki o'zgartirish "
        "so'rasa, ANIQ QANDAY KOD YOZISH KERAKLIGINI TAKLIF QILING (kod namunasi bilan), lekin "
        "HECH QACHON 'men buni tahrirladim' yoki shunga o'xshash deb aytmang — siz kodni "
        "AVTOMATIK O'ZGARTIRA OLMAYSIZ. Har doim tushuntirib bering: 'Tavsiya qilingan "
        "o'zgarishni qo'llash uchun, quyidagi kodni nusxalab, \"🛠 Botni tahrirlash\" -> "
        "\"📄 Kodni almashtirish\" (yoki requirements.txt uchun \"📋 requirements.txt "
        "almashtirish\") tugmasi orqali yuboring.'\n\n"
        "Javoblaringizni oddiy, tushunarli o'zbek tilida bering, ortiqcha uzun yozmang."
    )
