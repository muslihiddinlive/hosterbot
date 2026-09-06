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


async def _call_provider(provider: dict, system_prompt: str, user_prompt: str) -> str:
    """Bitta provayderga OpenAI-compatible /v1/chat/completions so'rovi yuboradi.
    Cloudflare Workers AI ham shu formatni to'liq qo'llab-quvvatlaydi
    (developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)."""
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"

    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 800,
    }

    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise AIError(f"{provider['name']}: HTTP {resp.status} — {body[:300]}")
            data = await resp.json()

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise AIError(f"{provider['name']}: kutilmagan javob formati — {e}")


async def ask_ai(system_prompt: str, user_prompt: str, telegram_id: int, bot_id: int = None) -> str:
    """Ustuvorlik tartibida faol provayderlarni birma-bir sinab ko'radi.
    Har bir muvaffaqiyatli/muvaffaqiyatsiz urinish ai_usage_log'ga yoziladi
    (kunlik limit hisoblash va admin uchun tarix uchun).

    Qaytaradi: AI javobi (matn).
    Ko'taradi: AIError — hech bir provayder ishlamasa yoki faol provayder umuman yo'q bo'lsa.
    """
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
            result = await _call_provider(provider, system_prompt, user_prompt)
            db.log_ai_usage(provider["provider_id"], telegram_id, bot_id, success=True)
            return result
        except Exception as e:
            log.warning(f"AI provider '{provider['name']}' xato qaytardi: {e}")
            db.log_ai_usage(provider["provider_id"], telegram_id, bot_id, success=False)
            last_error = e
            continue

    if last_error:
        raise AIError(f"Barcha AI provayderlar band yoki xato qaytardi. Oxirgi xato: {last_error}")
    raise AIError("Barcha AI provayderlar bugungi kunlik limitga yetgan — ertaga qayta urinib ko'ring.")


def build_crash_diagnosis_prompt(bot_label: str, log_text: str, code_snippet: str = "") -> tuple[str, str]:
    """Crash tashxis so'rovi uchun system+user promptlarni tayyorlaydi.
    Log matni oxirgi ~2000 belgigacha qisqartiriladi (token sarfini kamaytirish uchun) —
    odatda xato haqidagi eng muhim ma'lumot (traceback) log oxirida bo'ladi."""
    system_prompt = (
        "Siz Telegram bot hosting platformasidagi yordamchi diagnostsiz. Foydalanuvchi o'zi "
        "yuklagan bot kodi ishga tushmay qulab tushdi (crashed). Sizga oxirgi log va (bo'lsa) "
        "kod parchasi beriladi. Vazifangiz: xato sababini ODDIY, tushunarli o'zbek tilida "
        "tushuntirish va ANIQ, amaliy tuzatish qadamlarini berish. Texnik jargon ishlatmang, "
        "har bir tavsiyani oddiy foydalanuvchi (dasturchi bo'lmasligi mumkin) tushunadigan "
        "qilib yozing. Javobni 200-300 so'zdan oshirmang."
    )
    truncated_log = log_text[-2000:] if len(log_text) > 2000 else log_text
    user_prompt = f"Bot: {bot_label}\n\nOxirgi log:\n{truncated_log}"
    if code_snippet:
        user_prompt += f"\n\nKod parchasi (agar tegishli bo'lsa):\n{code_snippet[:1500]}"
    return system_prompt, user_prompt
