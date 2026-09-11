"""
services/github_deploy.py

Foydalanuvchi GitHub repo linkini yuborganda, botni fayl yuklash o'rniga
to'g'ridan-to'g'ri repo'dan deploy qilish uchun. Hozircha faqat PUBLIC
repolar qo'llab-quvvatlanadi — GitHub token kerak emas, oddiy HTTP orqali
zip arxivni yuklab olamiz (codeload.github.com).

Auto-redeploy: GitHub webhook orqali (foydalanuvchi repo Settings'da qo'lda
qo'shadi) — bu fayl faqat URL parse va ZIP yuklab olish qismini o'z ichiga
oladi, webhook qabul qilish logikasi main.py'da (aiohttp route).
"""
import os
import re
import secrets

import aiohttp

GITHUB_URL_RE = re.compile(
    r'^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$'
)

DOWNLOAD_TIMEOUT_SECONDS = 30


class GitHubDeployError(Exception):
    """Foydalanuvchiga ko'rsatiladigan, tushunarli xato xabari uchun."""


def parse_github_url(url: str) -> tuple[str, str]:
    """'https://github.com/owner/repo' (ixtiyoriy .git va oxirgi / bilan) dan
    (owner, repo) juftligini ajratadi. Mos kelmasa GitHubDeployError ko'taradi."""
    match = GITHUB_URL_RE.match(url.strip())
    if not match:
        raise GitHubDeployError(
            "Bu GitHub repo linkiga o'xshamayapti. To'g'ri format: "
            "https://github.com/owner/repo"
        )
    return match.group("owner"), match.group("repo")


async def _try_download_branch(session: aiohttp.ClientSession, owner: str, repo: str, branch: str, dest_path: str) -> bool:
    """Bitta branch nomini sinab ko'radi. Muvaffaqiyatli bo'lsa fayl yoziladi va True qaytadi."""
    url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
    async with session.get(url) as resp:
        if resp.status != 200:
            return False
        content = await resp.read()
        # GitHub topilmagan repo/branch uchun ham 200 bilan HTML xato sahifa
        # qaytarishi mumkin — haqiqiy ZIP ekanligini signature orqali tekshiramiz.
        if not content.startswith(b"PK"):
            return False
        with open(dest_path, "wb") as f:
            f.write(content)
        return True


async def download_repo_zip(owner: str, repo: str, dest_path: str, branch: str = None) -> str:
    """Repo'ni ZIP sifatida yuklab, dest_path'ga yozadi. Aniq branch berilmasa,
    'main' va 'master'ni birma-bir sinaydi (GitHub'ning ikkala standart nomi).
    Qaytaradi: haqiqiy ishlatilgan branch nomi.
    Ko'taradi: GitHubDeployError — repo topilmasa yoki private bo'lsa (public
    bo'lmagan repo uchun ham xuddi shu xato — farqini bila olmaymiz, chunki
    token ishlatmaymiz)."""
    timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS)
    branches_to_try = [branch] if branch else ["main", "master"]

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for b in branches_to_try:
            try:
                if await _try_download_branch(session, owner, repo, b, dest_path):
                    return b
            except aiohttp.ClientError as e:
                raise GitHubDeployError(f"GitHub'ga ulanishda xato: {e}")

    raise GitHubDeployError(
        f"Repo topilmadi yoki yuklab bo'lmadi: github.com/{owner}/{repo}. "
        f"Repo PUBLIC ekanligini va {'/'.join(branches_to_try)} branch(lar)dan "
        f"birida kod borligini tekshiring."
    )


def generate_webhook_secret() -> str:
    """Har bir bot uchun noyob webhook token — URL topilib qolsa ham,
    tasodifiy odam botni qayta deploy qila olmasin uchun."""
    return secrets.token_urlsafe(24)
