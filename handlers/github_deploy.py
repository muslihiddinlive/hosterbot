"""
handlers/github_deploy.py

'🐙 GitHub repo orqali deploy qilish' — foydalanuvchi fayl yuklash o'rniga
public GitHub repo linkini yuboradi. Bot repo'ni ZIP sifatida (codeload.github.com
orqali, token kerak emas) yuklab oladi, so'ng handlers/add_bot.py'dagi
_process_downloaded_code() orqali xuddi zip-upload qilingandek davom etadi
(kod ikki marta yozilmasin uchun).

Har bir GitHub orqali deploy qilingan bot uchun noyob webhook_secret
generatsiya qilinadi (deploy tugagach add_bot.py foydalanuvchiga webhook
URL'ni ko'rsatadi) — main.py'dagi /gh-webhook/{bot_id}/{secret} route orqali
GitHub push kelganda avtomatik qayta deploy qilinadi (cb_bot_rebuild bilan
bir xil qayta-build+start mantig'i, lekin avval repo qayta yuklab olinadi).
"""
import os
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import GitHubDeploy, AddBot
from keyboards import cancel_kb
from services.github_deploy import parse_github_url, download_repo_zip, generate_webhook_secret, GitHubDeployError
from handlers.add_bot import _process_downloaded_code

router = Router()
log = logging.getLogger("hosterbot.github_deploy")


@router.callback_query(F.data == "deploy_from_github")
async def cb_deploy_from_github(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != AddBot.waiting_code.state:
        # Foydalanuvchi eski/eskirgan tugmani bossa (masalan boshqa oqimni
        # allaqachon boshlab qo'ygan bo'lsa) — chalkashlikni oldini olamiz.
        await callback.answer("Bu tugma eskirgan. \"➕ Bot qo'shish\"ni qaytadan bosing.", show_alert=True)
        return

    await state.set_state(GitHubDeploy.waiting_url)
    await callback.message.answer(
        "🐙 GitHub repo linkini yuboring (faqat <b>public</b> repo qo'llab-quvvatlanadi):\n\n"
        "Masalan: <code>https://github.com/owner/repo</code>",
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )
    await callback.answer()


@router.message(GitHubDeploy.waiting_url)
async def receive_github_url(message: Message, state: FSMContext, bot: Bot):
    url = (message.text or "").strip()
    try:
        owner, repo = parse_github_url(url)
    except GitHubDeployError as e:
        await message.answer(str(e))
        return

    status_msg = await message.answer(f"⏳ <code>{owner}/{repo}</code> yuklab olinmoqda...", parse_mode="HTML")

    tmp_dir = f"/tmp/github_{message.from_user.id}_{message.message_id}"
    os.makedirs(tmp_dir, exist_ok=True)
    zip_path = os.path.join(tmp_dir, f"{repo}.zip")

    try:
        branch = await download_repo_zip(owner, repo, zip_path)
    except GitHubDeployError as e:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await status_msg.edit_text(f"❌ {e}")
        return
    except Exception as e:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        log.exception("GitHub ZIP yuklab olishda kutilmagan xato")
        await status_msg.edit_text(f"❌ Yuklab olishda kutilmagan xato: {e}")
        return

    await status_msg.edit_text(f"✅ <code>{owner}/{repo}</code> (branch: {branch}) yuklab olindi.", parse_mode="HTML")

    github_meta = {
        "github_url": f"https://github.com/{owner}/{repo}",
        "github_branch": branch,
        "webhook_secret": generate_webhook_secret(),
    }
    # GitHub ZIP har doim .zip sifatida keladi (repo strukturasi — bir nechta
    # fayl/papka), shu sabab is_zip=True bilan mavjud zip-oqimiga qo'shiladi.
    await _process_downloaded_code(
        message, state, zip_path, tmp_dir, f"{repo}.zip", is_zip=True,
        storage_file_id=None, github_meta=github_meta,
    )
