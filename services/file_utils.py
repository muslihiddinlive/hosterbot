"""
services/file_utils.py
Yuklangan .py yoki .zip faylni tekshirish, tilini aniqlash, diskka joylash.
"""
import os
import zipfile
import shutil

from config import DATA_DIR

LANGUAGE_MARKERS = {
    "python": ["requirements.txt", ".py"],
    "node": ["package.json", ".js", ".ts"],
    "php": ["composer.json", ".php"],
    "go": ["go.mod", ".go"],
    "java": ["pom.xml", "build.gradle", ".java"],
}


def detect_language_from_zip(zip_path: str) -> str:
    """Zip ichidagi fayllarga qarab tilni taxminiy aniqlaydi."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    lower_names = [n.lower() for n in names]

    if any(n.endswith("requirements.txt") for n in lower_names):
        return "python"
    if any(n.endswith("package.json") for n in lower_names):
        return "node"
    if any(n.endswith("composer.json") for n in lower_names):
        return "php"
    if any(n.endswith("go.mod") for n in lower_names):
        return "go"
    if any(n.endswith("pom.xml") or n.endswith("build.gradle") for n in lower_names):
        return "java"

    # extension-based fallback
    ext_count = {}
    for n in lower_names:
        _, ext = os.path.splitext(n)
        if ext:
            ext_count[ext] = ext_count.get(ext, 0) + 1
    if ext_count:
        top_ext = max(ext_count, key=ext_count.get)
        mapping = {".py": "python", ".js": "node", ".ts": "node", ".php": "php", ".go": "go", ".java": "java"}
        return mapping.get(top_ext, "unknown")
    return "unknown"


def extract_zip(zip_path: str, dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        # Zip-slip himoyasi: har bir yo'lni tekshirib chiqamiz
        for member in z.namelist():
            member_path = os.path.normpath(os.path.join(dest_dir, member))
            if not member_path.startswith(os.path.abspath(dest_dir)):
                raise ValueError("Xavfli zip fayl: yo'l chegaradan chiqib ketmoqda")
        z.extractall(dest_dir)


import re

REQUIREMENTS_PATTERN = re.compile(r"^requirements(\.txt)+$", re.IGNORECASE)


def find_requirements_txt(root_dir: str) -> str | None:
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if REQUIREMENTS_PATTERN.match(fname):
                return os.path.join(dirpath, fname)
    return None


def normalize_requirements_filename(root_dir: str) -> str | None:
    """
    Ba'zi fayl menejerlar (masalan Windows Notepad "Save As") requirements.txt
    nomini requirements.txt.txt qilib saqlab qo'yishi mumkin. Shu funksiya bunday
    fayllarni topib, standart "requirements.txt" nomiga o'zgartiradi — build buyrug'i
    foydalanuvchi nima yozgan bo'lishidan qat'iy nazar ishlashi uchun.
    Topilgan (va kerak bo'lsa qayta nomlangan) faylning yo'lini qaytaradi.
    """
    found = find_requirements_txt(root_dir)
    if found is None:
        return None
    correct_path = os.path.join(os.path.dirname(found), "requirements.txt")
    if found != correct_path:
        os.rename(found, correct_path)
        return correct_path
    return found


def list_py_files_in_zip(zip_path: str, limit: int = 15) -> list[str]:
    """Zip ichidagi .py fayllarni (chuqurlik bo'yicha) qaytaradi — foydalanuvchiga
    start buyrug'ini yozishda aniq fayl nomini ko'rsatish uchun."""
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".py") and not n.startswith("__MACOSX")]
    names.sort(key=lambda n: n.count("/"))  # eng tepadagi fayllar avval
    return names[:limit]


def resolve_project_root(extract_dir: str) -> str:
    """
    Ko'p zip'lar ichida bitta "wrapper" papka bo'ladi (masalan GitHub'dan yuklab
    olingan zip -> myproject-main/...). Bu holda requirements.txt/bot.py o'sha
    ichki papkada bo'ladi, tashqarida emas. Shu funksiya haqiqiy loyiha papkasini
    (build/start buyruqlari shu yerdan ishga tushishi kerak bo'lgan papkani) topadi:
    agar extract_dir ichida FAQAT bitta narsa bo'lsa va u papka bo'lsa, o'sha
    papkani (rekursiv ravishda, bir necha qatlam bo'lsa ham) qaytaradi.
    """
    current = extract_dir
    while True:
        entries = [e for e in os.listdir(current) if not e.startswith("__MACOSX")]
        if len(entries) == 1 and os.path.isdir(os.path.join(current, entries[0])):
            current = os.path.join(current, entries[0])
            continue
        break
    return current


def resolve_start_command(start_cmd: str, workdir: str) -> str:
    """
    Agar start buyrug'i "python <nom>.py" ko'rinishida bo'lsa-yu, shu nomdagi fayl
    workdir'da topilmasa, va workdir tepasida ANIQ BITTA .py fayl bo'lsa — buyruqni
    o'sha faylga avtomatik moslashtiradi (nom xato yozilgan bo'lishi mumkin: bo'shliq,
    katta-kichik harf, kengaytma va h.k.).
    """
    match = re.search(r'([^\s"\']+\.py)', start_cmd)
    if not match:
        return start_cmd
    referenced = match.group(1)
    if os.path.exists(os.path.join(workdir, referenced)):
        return start_cmd  # to'g'ri, tegmaymiz

    py_files = [f for f in os.listdir(workdir) if f.lower().endswith(".py")]
    if len(py_files) == 1:
        actual = py_files[0]
        return start_cmd.replace(referenced, f'"{actual}"')
    return start_cmd  # bir nechta .py bor — avtomatik tanlay olmaymiz, tegmaymiz


def fix_py_encoding(path: str) -> str | None:
    """
    Fayl UTF-8 bo'lmasa (masalan Windows'da cp1251/cp1252 bilan saqlangan bo'lsa),
    haqiqiy kodировkani aniqlab, UTF-8'ga o'giradi. Muvaffaqiyatli bo'lsa aniqlangan
    original kodировka nomini qaytaradi, fayl allaqachon UTF-8 bo'lsa None qaytaradi,
    aniqlab bo'lmasa ValueError ko'taradi.
    """
    with open(path, "rb") as f:
        raw = f.read()

    try:
        raw.decode("utf-8")
        return None  # allaqachon UTF-8, tegishning hojati yo'q
    except UnicodeDecodeError:
        pass

    from charset_normalizer import from_bytes
    result = from_bytes(raw).best()
    if result is None:
        raise ValueError("Fayl kodировkasini aniqlab bo'lmadi")

    text = str(result)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return result.encoding


def fix_all_py_encodings(root_dir: str) -> dict:
    """Papka ichidagi barcha .py fayllarni tekshirib, UTF-8 emaslarini tuzatadi.
    {"fixed": [(fayl, eski_encoding), ...], "failed": [fayl, ...]} qaytaradi."""
    fixed, failed = [], []
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if fname.endswith(".py"):
                fpath = os.path.join(dirpath, fname)
                try:
                    old_enc = fix_py_encoding(fpath)
                    if old_enc:
                        fixed.append((os.path.relpath(fpath, root_dir), old_enc))
                except ValueError:
                    failed.append(os.path.relpath(fpath, root_dir))
    return {"fixed": fixed, "failed": failed}


import ast
import sys

# Ba'zi paketlarning PyPI nomi import nomidan farq qiladi (masalan
# "python-telegram-bot" -> import telegram, "pyTelegramBotAPI" -> import telebot).
# To'liq ro'yxat emas, faqat eng ko'p uchraydigan hollar uchun ogohlantirish matnida
# to'g'ri nom ko'rsatish uchun.
_IMPORT_TO_PACKAGE = {
    "telegram": "python-telegram-bot",
    "telebot": "pyTelegramBotAPI",
    "aiogram": "aiogram",
    "aiosqlite": "aiosqlite",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "sklearn": "scikit-learn",
    "requests": "requests",
    "aiohttp": "aiohttp",
    "flask": "Flask",
    "django": "Django",
    "sqlalchemy": "SQLAlchemy",
    "pymongo": "pymongo",
    "psycopg2": "psycopg2-binary",
}


def _stdlib_module_names() -> set[str]:
    names = set(getattr(sys, "stdlib_module_names", ()))
    # ast'da har doim ham to'liq bo'lmasligi mumkin bo'lgan, lekin amalda
    # standart hisoblanadigan ba'zi ichki/eski nomlar uchun qo'shimcha:
    names |= {"_thread", "__future__"}
    return names


def detect_external_imports(code_text: str) -> list[str]:
    """
    Berilgan Python kodidagi standart kutubxonaga KIRMAYDIGAN top-level
    importlarni aniqlaydi (masalan "aiogram", "aiosqlite"). Kod sintaksis
    xatosi bo'lsa yoki hech narsa topilmasa, bo'sh ro'yxat qaytaradi — bu
    faqat ogohlantirish uchun, build jarayonini bloklamaydi.
    Har bir element PyPI'dagi (taxminiy) o'rnatish nomida qaytariladi
    (masalan import "telegram" -> "python-telegram-bot").
    """
    try:
        tree = ast.parse(code_text)
    except SyntaxError:
        return []

    stdlib = _stdlib_module_names()
    found_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # nisbiy import (masalan "from . import x") — loyihaning o'zi
            if node.module:
                found_modules.add(node.module.split(".")[0])

    external = sorted(
        m for m in found_modules
        if m not in stdlib and m not in ("__main__",)
    )
    return [_IMPORT_TO_PACKAGE.get(m, m) for m in external]


def bot_workdir(bot_id: int) -> str:
    path = os.path.join(DATA_DIR, f"bot_{bot_id}")
    os.makedirs(path, exist_ok=True)
    return path


def write_env_file(bot_id: int, env_pairs: dict) -> str:
    workdir = bot_workdir(bot_id)
    env_path = os.path.join(workdir, ".env")
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in env_pairs.items():
            f.write(f"{k}={v}\n")
    return env_path


def cleanup_bot_files(bot_id: int):
    path = os.path.join(DATA_DIR, f"bot_{bot_id}")
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


_TOKEN_RE = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{30,40}\b')
_ID_VAR_RE = re.compile(
    r'(?i)\b(chat_id|admin_id|owner_id|user_id|group_id|channel_id)\b\s*[:=]\s*["\']?(-?\d{6,15})["\']?'
)


def detect_credentials(workdir: str) -> list[dict]:
    """
    Kod fayllari (.py) ichidan Telegram bot token va chat/admin ID'ga o'xshagan
    qatorlarni qidiradi (regex bilan, o'zgaruvchi nomiga qarab: chat_id, admin_id,
    owner_id va h.k.). Har bir topilma uchun fayl nomi va qator raqamini ham
    qaytaradi — bu admin uchun "qaysi faylda, nechanchi qatorda" ma'lumotini beradi.

    Bu foydalanuvchi ENV o'rniga tokenni to'g'ridan-to'g'ri kodga yozib qo'ygan
    hollarda ham (ENV orqali emas) botning username'ini aniqlash va admin uchun
    shaffoflik uchun ishlatiladi. Yolg'on-musbat (false positive) ehtimoli bor —
    shuning uchun bu faqat yordamchi/taxminiy aniqlash, kafolat emas.
    """
    results = []
    seen = set()
    for root, _, files in os.walk(workdir):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, workdir)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, start=1):
                        for m in _TOKEN_RE.finditer(line):
                            key = ("token", m.group(0))
                            if key not in seen:
                                seen.add(key)
                                results.append({"type": "token", "value": m.group(0), "file": rel, "line": i})
                        for m in _ID_VAR_RE.finditer(line):
                            key = ("chat_id", m.group(2))
                            if key not in seen:
                                seen.add(key)
                                results.append({
                                    "type": "chat_id", "value": m.group(2), "var_name": m.group(1),
                                    "file": rel, "line": i,
                                })
            except Exception:
                continue
    return results
