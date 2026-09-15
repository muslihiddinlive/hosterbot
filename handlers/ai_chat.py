"""
handlers/ai_chat.py

Muammo: agar foydalanuvchi hech qanday menyu tugmasini bosmasdan, oddiy matn
yozsa (masalan savol yoki xato bilan yozilgan buyruq), bot JIM QOLARDI — hech
qanday handler mos kelmagani uchun xabar e'tiborsiz qolib ketardi.

Yechim: bu modul ENG OXIRIDA ro'yxatdan o'tadi (main.py'da eng oxirgi router)
va faqat boshqa hech qanday handler ushlamagan matn xabarlarini qabul qiladi.
Bunday holatda "AI'ga yozyapsizmi?" deb so'raydi:
- "Yo'q" -> "bunday buyruq topilmadi" xabarini ko'rsatadi
- "Ha" -> erkin AI suhbat rejimiga o'tadi (AIChat.chatting state)

Erkin suhbat rejimida AI botning kodi/requirements.txt'ni O'QIY va TUSHUNTIRA
oladi (bepul emas — har xabar uchun ai_chat_price_stars, default 1⭐️). Agar AI
aniq bir tahrirlashni TAKLIF QILSA (function-calling orqali, EDIT_FILE_TOOL),
foydalanuvchiga alohida tasdiqlash so'raladi — FAQAT "Ha, tuzat" bosilgandan
keyin haqiqiy fayl o'zgaradi (qo'shimcha ai_help_price_stars narxi bilan).
"""
import html
import logging
import os
import re

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, FSInputFile, LabeledPrice
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter

import database as db
from config import is_admin, STORAGE_GROUP_ID
from states import AIChat
from keyboards import ai_chat_confirm_kb, ai_chat_pick_bot_kb, ai_chat_edit_confirm_kb, main_menu_kb, cancel_kb
from services.ai_client import ask_ai_with_tools, build_free_chat_system_prompt, AIError
from services.deploy_manager import static_scan
from handlers.bot_actions import _rebuild_and_start

router = Router()
log = logging.getLogger("hosterbot.ai_chat")

MAX_HISTORY_MESSAGES = 12  # user+assistant juftlik sifatida ~6 ta oldingi almashinuv

# AIChat.chatting davomida reply-klaviaturada FAQAT "⛔️ Bekor qilish" ko'rsatiladi
# (cancel_kb) — bu chiqish yo'lini yagona va aniq qiladi. Boshqa menyu
# tugmalari (masalan "🤖 Mening botlarim") ham matn sifatida kelib qolishi
# mumkin bo'lgan holatlar uchun (masalan foydalanuvchi ilgari saqlangan
# klaviaturadan foydalansa) qo'shimcha ehtiyot chorasi sifatida ro'yxatga
# olingan — ular ham AI'ga yuborilmasdan, state tozalanadi.
_MENU_BUTTON_TEXTS = {
    "⛔️ Bekor qilish", "❓ Yordam", "➕ Bot qo'shish", "💳 Hisob",
    "📩 Adminga habar berish", "🗄 DB backup tekshirish", "🛠 Admin panel",
    "🤖 Mening botlarim",
}


def _gather_bot_context(bot_row) -> tuple[str, str]:
    """Kod parchasi va requirements.txt tarkibini o'qib qaytaradi (cb_ai_help
    bilan bir xil naqsh) — AI suhbat konteksti uchun."""
    code_snippet = ""
    try:
        match = re.search(r'([^\s"\']+\.py)', bot_row["start_cmd"] or "")
        py_name = os.path.basename(match.group(1)) if match else None
        if py_name:
            py_path = os.path.join(bot_row["code_path"], py_name)
            if os.path.isfile(py_path):
                with open(py_path, "r", encoding="utf-8", errors="ignore") as f:
                    code_snippet = f.read()
    except Exception:
        pass

    requirements_text = ""
    try:
        requirements_path = os.path.join(bot_row["code_path"], "requirements.txt")
        if os.path.isfile(requirements_path):
            with open(requirements_path, "r", encoding="utf-8", errors="ignore") as f:
                requirements_text = f.read()
    except Exception:
        pass

    return code_snippet, requirements_text


# ---------- Fallback: hech qanday tugma/buyruq bilan mos kelmagan matn ----------

@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def fallback_free_text(message: Message, state: FSMContext):
    # DIQQAT: bu handler ENG OXIRIDA ro'yxatdan o'tishi SHART (main.py) — aks
    # holda boshqa barcha reply-tugma va state handler'larini "yutib" qo'yardi.
    #
    # MUHIM FIX: ilgari state tekshiruvi handler ICHIDA ("if current_state is
    # not None: return") qilinardi. aiogram 3.x'da handler bir marta chaqirilib,
    # oddiy `return` bilan tugasa ham, dispatcher buni "update ushlandi" deb
    # hisoblaydi va router ichidagi KEYINGI handler'larga (masalan pastdagi
    # AIChat.chatting uchun handle_ai_chat_message) umuman yetib bormaydi.
    # Natijada AI suhbat boshlangandan keyin yozilgan HAR QANDAY xabar shu
    # yerda "yutilib" ketardi — javob kelmasdi. Endi StateFilter(None) orqali
    # deklarativ tekshiramiz: state band bo'lsa, aiogram avtomatik ravishda
    # navbatdagi mos handler'ni sinab ko'radi.
    await state.update_data(ai_pending_text=message.text)
    await message.answer(
        "🤖 Bu buyruqni tushunmadim. AI'ga yozmoqchimisiz?",
        reply_markup=ai_chat_confirm_kb(),
    )


@router.callback_query(F.data.startswith("ai_chat_confirm:"))
async def cb_ai_chat_confirm(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split(":", 1)[1]
    if choice == "no":
        await callback.message.edit_text("Bunday buyruq topilmadi. Asosiy menyudan foydalaning.")
        await callback.answer()
        return

    bots = db.list_user_bots(callback.from_user.id)
    if not bots:
        await callback.message.edit_text(
            "Hozircha hech qanday botingiz yo'q, shu sabab AI suhbati uchun kontekst yo'q. "
            "Avval \"➕ Bot qo'shish\" orqali bot deploy qiling."
        )
        await callback.answer()
        return

    if len(bots) == 1:
        await _start_ai_chat(callback, state, bots[0]["bot_id"])
        return

    await state.set_state(AIChat.choosing_bot)
    await callback.message.edit_text(
        "🤖 Qaysi bot haqida gaplashmoqchisiz?",
        reply_markup=ai_chat_pick_bot_kb(bots),
    )
    await callback.answer()


@router.callback_query(AIChat.choosing_bot, F.data.startswith("ai_chat_pick_bot:"))
async def cb_ai_chat_pick_bot(callback: CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split(":", 1)[1])
    await _start_ai_chat(callback, state, bot_id)


@router.callback_query(F.data == "ai_chat_cancel")
async def cb_ai_chat_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Bekor qilindi.")
    await callback.answer()


async def _start_ai_chat(callback: CallbackQuery, state: FSMContext, bot_id: int):
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        await callback.answer("Ruxsat yo'q yoki bot topilmadi.", show_alert=True)
        return

    await state.update_data(ai_chat_bot_id=bot_id, ai_chat_history=[])
    await state.set_state(AIChat.chatting)
    price = db.get_ai_chat_price_stars()
    bot_label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
    await callback.message.edit_text(
        f"🤖 <b>{html.escape(bot_label)}</b> haqida AI bilan suhbat boshlandi.\n\n"
        f"Har bir xabar <b>{price}⭐️</b> turadi. Kod yoki requirements.txt haqida savol bering — "
        f"AI o'qib tushuntiradi. Agar tuzatish kerak bo'lsa, AI taklif beradi va SIZNING "
        f"roziligingiz bilan (qo'shimcha narx bilan) o'zi tahrirlaydi.",
        parse_mode="HTML",
    )
    # edit_text orqali reply-klaviaturani (pastdagi) o'zgartirib bo'lmaydi — shu
    # sabab alohida xabar bilan "⛔️ Bekor qilish" tugmasini ko'rsatamiz, bu
    # suhbatdan chiqishning yagona, aniq yo'li bo'ladi.
    await callback.message.answer("Chiqish uchun pastdagi tugmani bosing:", reply_markup=cancel_kb())
    await callback.answer()

    # YANGI FIX: agar foydalanuvchi "Ha" bosishdan OLDIN allaqachon savolini
    # yozgan bo'lsa (fallback_free_text orqali saqlangan), uni endi qayta
    # yozdirmasdan AVTOMATIK yuboramiz — ilgari bu matn yo'qolib, foydalanuvchi
    # suhbat boshlangach savolni QAYTA yozishga majbur bo'lardi.
    data = await state.get_data()
    pending_text = data.get("ai_pending_text")
    if pending_text:
        await state.update_data(ai_pending_text=None)
        await _process_ai_chat_text(callback.message, state, callback.bot, callback.from_user.id, pending_text)


# ---------- Suhbat rejimidagi xabarlar ----------

async def _process_ai_chat_text(answer_target: Message, state: FSMContext, bot: Bot, user_id: int, text: str):
    """
    AI suhbat xabarini qayta ishlaydi. answer_target — javoblar shu chat'ga
    yuboriladigan istalgan Message obyekti (foydalanuvchining o'zi yozgan
    xabari, YOKI botning oldingi xabari — ikkalasida ham .answer() bir xil
    ishlaydi). user_id alohida beriladi, chunki answer_target ba'zan botning
    o'z xabari bo'lishi mumkin (uning from_user'i botning o'zi bo'lardi).

    YANGI: bu funksiya endi ikki joydan chaqiriladi — (1) oddiy xabar kelganda
    (handle_ai_chat_message), (2) "AI'ga yozmoqchimisiz? Ha" bosilgandan keyin,
    foydalanuvchi ALLAQACHON yozgan matn bilan (_start_ai_chat) — ilgari bu
    holatda matn yo'qolib, foydalanuvchi qayta yozishga majbur bo'lardi.
    """
    if (text or "") in _MENU_BUTTON_TEXTS:
        await state.clear()
        await answer_target.answer(
            "✅ AI suhbatidan chiqdingiz. Davom etish uchun tugmani yana bir marta bosing.",
            reply_markup=main_menu_kb(is_admin=is_admin(user_id)),
        )
        return

    data = await state.get_data()
    bot_id = data.get("ai_chat_bot_id")
    bot_row = db.get_bot(bot_id) if bot_id else None
    if bot_row is None:
        await state.clear()
        await answer_target.answer("Bot topilmadi, suhbat yakunlandi.", reply_markup=main_menu_kb(is_admin=is_admin(user_id)))
        return

    price = 0 if is_admin(user_id) else db.get_ai_chat_price_stars()
    owner_id = bot_row["owner_id"]
    balance = db.get_user_balance(owner_id)
    if balance < price:
        # YANGI: ilgari bu yerda suhbat butunlay yakunlanardi ("balansingiz
        # yetarli emas") va foydalanuvchi qo'lda "💳 Hisob"ga o'tib, to'lab,
        # keyin QAYTA savol yozishi kerak edi. Endi shortfall miqdoriga
        # to'g'ridan-to'g'ri shu yerning o'zida Stars invoice chiqariladi —
        # to'lov muvaffaqiyatli bo'lsa, savol AVTOMATIK qayta yuboriladi
        # (process_successful_payment orqali, stars.py'da).
        shortfall = price - balance
        await state.update_data(ai_pending_text=text)  # suhbat holati saqlanadi, YAKUNLANMAYDI
        await answer_target.answer(
            f"⭐️ Balansingiz yetarli emas ({balance}/{price}). Kerakli <b>{shortfall}⭐️</b>ni "
            f"to'lang — to'lov o'tishi bilan savolingiz avtomatik yuboriladi:",
            parse_mode="HTML",
        )
        await answer_target.answer_invoice(
            title=f"{shortfall} ⭐️ Stars — AI suhbat",
            description=f"AI suhbatni davom ettirish uchun {shortfall} Stars.",
            payload=f"ai_chat_topup_{owner_id}_{shortfall}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=f"{shortfall} Stars", amount=shortfall)],
        )
        return

    bot_label = bot_row["bot_username"] or bot_row["display_name"] or f"Bot #{bot_id}"
    thinking_msg = await answer_target.answer("🤖 O'ylanyapman...")

    history = data.get("ai_chat_history", [])
    code_snippet, requirements_text = _gather_bot_context(bot_row)

    system_prompt = build_free_chat_system_prompt(bot_label)
    context_note = (
        f"Botning joriy kodi:\n{code_snippet[:3000] if code_snippet else '(kod o‘qib bo‘lmadi)'}\n\n"
        f"requirements.txt:\n{requirements_text[:800] if requirements_text else '(fayl yo‘q yoki bo‘sh)'}"
    )
    messages = (
        [{"role": "system", "content": system_prompt}]
        + history[-MAX_HISTORY_MESSAGES:]
        + [{"role": "user", "content": f"{context_note}\n\nSavol: {text}"}]
    )

    try:
        ai_message = await ask_ai_with_tools(messages, telegram_id=owner_id, bot_id=bot_id)
    except AIError as e:
        log.warning(f"AI chat xatosi (bot_id={bot_id}): {e}")
        # DIQQAT: str(e) provider nomi/xom HTTP javobini o'z ichiga olishi mumkin —
        # bu ichki infratuzilma tafsiloti, foydalanuvchiga ko'rsatilmasligi kerak.
        await thinking_msg.edit_text("⚠️ AI hozircha ishlamayapti, keyinroq qayta urinib ko'ring.\n\nStars sarflanmadi.")
        return

    if price > 0:
        # Faqat AI muvaffaqiyatli javob qaytargandan KEYIN suhbat narxini yechamiz.
        db.add_user_balance(owner_id, -price, reason=f"AI suhbat (bot #{bot_id})")
        from services.backup import backup_database
        await backup_database(bot)
        price_note = f"\n\n<i>-{price}⭐️ yechildi. Qolgan balans: {db.get_user_balance(owner_id)}⭐️</i>"
    else:
        price_note = "\n\n<i>🎁 VIP (admin) — bepul.</i>"

    tool_calls = ai_message.get("tool_calls") or []
    edit_tool_call = next((tc for tc in tool_calls if tc.get("function", {}).get("name") == "propose_file_edit"), None)

    if edit_tool_call:
        import json
        try:
            args = json.loads(edit_tool_call["function"]["arguments"])
        except (KeyError, ValueError, TypeError):
            args = {}
        target = args.get("target")
        new_content = args.get("new_content", "")
        explanation = args.get("explanation", "")

        if target not in ("code", "requirements") or not new_content:
            # AI noto'g'ri formatda tool chaqirgan bo'lsa — xavfsiz tomonga o'tamiz,
            # oddiy matn javob sifatida ko'rsatamiz, fayl tahrirlashga urinmaymiz.
            await thinking_msg.edit_text(
                f"🤖 {html.escape(ai_message.get('content') or explanation or 'Javob olindi, lekin format tushunarsiz.')}"
                f"{price_note}",
                parse_mode="HTML",
            )
        else:
            edit_price = 0 if is_admin(user_id) else db.get_ai_help_price_stars()
            await state.update_data(
                ai_chat_pending_edit={"target": target, "new_content": new_content, "bot_id": bot_id},
            )
            target_label = "kod (.py) fayli" if target == "code" else "requirements.txt"
            edit_price_label = f"qo'shimcha {edit_price}⭐️" if edit_price > 0 else "bepul, VIP"
            await thinking_msg.edit_text(
                f"🤖 <b>Tuzatish taklifi — {target_label}</b>\n\n{html.escape(explanation)}\n\n"
                f"Bu o'zgarishni SIZNING roziligingiz bilan qo'llash mumkin ({edit_price_label}).",
                parse_mode="HTML",
                reply_markup=ai_chat_edit_confirm_kb(edit_price),
            )
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": ai_message.get("content") or f"[Tahrirlash taklif qilindi: {target}]"})
    else:
        reply_text = (ai_message.get("content") or "").strip() or "Javob bo'sh keldi."
        await thinking_msg.edit_text(
            f"🤖 {html.escape(reply_text)}{price_note}",
            parse_mode="HTML",
        )
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply_text})

    await state.update_data(ai_chat_history=history[-MAX_HISTORY_MESSAGES:], ai_pending_text=None)


@router.message(AIChat.chatting)
async def handle_ai_chat_message(message: Message, state: FSMContext, bot: Bot):
    await _process_ai_chat_text(message, state, bot, message.from_user.id, message.text or "")


@router.callback_query(F.data.startswith("ai_chat_apply_edit:"))
async def cb_ai_chat_apply_edit(callback: CallbackQuery, state: FSMContext, bot: Bot):
    choice = callback.data.split(":", 1)[1]
    data = await state.get_data()
    pending = data.get("ai_chat_pending_edit")

    if choice == "no" or pending is None:
        await state.update_data(ai_chat_pending_edit=None)
        await callback.message.edit_text("Bekor qilindi — hech narsa o'zgartirilmadi. Suhbatni davom ettirishingiz mumkin.")
        await callback.answer()
        return

    bot_id = pending["bot_id"]
    bot_row = db.get_bot(bot_id)
    if bot_row is None or (bot_row["owner_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        await callback.answer("Ruxsat yo'q yoki bot topilmadi.", show_alert=True)
        return

    edit_price = 0 if is_admin(callback.from_user.id) else db.get_ai_help_price_stars()
    owner_id = bot_row["owner_id"]
    balance = db.get_user_balance(owner_id)
    if balance < edit_price:
        await callback.answer(f"⭐️ Balansingiz yetarli emas ({balance}/{edit_price}).", show_alert=True)
        return

    await callback.answer("Qo'llanmoqda...")
    target = pending["target"]
    new_content = pending["new_content"]

    try:
        if target == "code":
            match = re.search(r'([^\s"\']+\.py)', bot_row["start_cmd"] or "")
            py_name = os.path.basename(match.group(1)) if match else "main.py"
            target_path = os.path.join(bot_row["code_path"], py_name)
        else:
            target_path = os.path.join(bot_row["code_path"], "requirements.txt")
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        log.exception("AI taklif qilgan tahrirlashni yozishda xato")
        await callback.message.answer(f"⚠️ Faylni yozishda xato: {e}")
        return

    # XAVFSIZLIK FIX: AI taklif qilgan kod ilgari static_scan'siz to'g'ridan-to'g'ri
    # yozilib, avtomatik ishga tushirilardi. AI provayder — ishonchsiz manba
    # (masalan agar boshqa foydalanuvchi suhbat orqali AI'ni zararli kod taklif
    # qilishga undasa/manipulyatsiya qilsa), shu sabab add_bot.py'dagi bilan bir
    # xil tekshiruvni shu yerga ham qo'shamiz. Bloklamaymiz (dizayn bo'yicha —
    # egasi allaqachon "Ha, tuzat" bosib rozilik bergan), faqat ogohlantiramiz.
    warn_text = ""
    if target == "code":
        try:
            warnings = static_scan(new_content)
        except Exception:
            warnings = []
        if warnings:
            warn_text = "\n\n⚠️ Yangi kodda diqqat talab qiladigan qatorlar aniqlandi."

    # MUHIM FIX: yangi fayl (kod yoki requirements.txt) ilgari FAQAT workdir'ga
    # yozilib, hech qachon STORAGE_GROUP'ga backup qilinmasdi — platforma qayta
    # ishga tushganda AI tahrirlagan o'zgarish yo'qolib, eski fayl tiklanardi
    # (fix_code/fix_reqs'da topilgan xato bilan bir xil sabab).
    try:
        sent = await bot.send_document(
            STORAGE_GROUP_ID, FSInputFile(target_path),
            caption=f"{os.path.basename(target_path)} (AI tahrirlagan) — Bot #{bot_id}, Owner: {owner_id}",
        )
        if target == "code":
            db.set_storage_file_id(bot_id, sent.document.file_id, is_zip=False)
        else:
            db.set_requirements_file_id(bot_id, sent.document.file_id)
    except Exception:
        log.exception("AI tahrirlagan faylni STORAGE_GROUP'ga backup qilishda xato")

    if edit_price > 0:
        # Fayl muvaffaqiyatli yozilgandan KEYIN Stars yechamiz (bepul urinishlar
        # uchun pul olinmasligi kerak degan qoida bilan bir xil).
        db.add_user_balance(owner_id, -edit_price, reason=f"AI tahrirlash qo'llandi (bot #{bot_id})")
        from services.backup import backup_database
        await backup_database(bot)
        applied_note = f"(-{edit_price}⭐️)"
    else:
        applied_note = "(🎁 VIP, bepul)"

    await state.update_data(ai_chat_pending_edit=None)
    await callback.message.edit_text(
        f"✅ O'zgarish qo'llandi {applied_note}.{warn_text} Qayta build va ishga tushirilmoqda..."
    )
    await _rebuild_and_start(bot_id, bot, callback.message)
