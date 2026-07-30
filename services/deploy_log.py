"""
services/deploy_log.py

Muammo: run.log faylida hammasi aralash-quralash edi — foydalanuvchi botining o'z
stdout/stderr chiqishi bilan platformaning build/start bosqichlari bir-biriga
qorishib ketardi. Bu odam (yoki AI) uchun "nima uchun xato bo'ldi" ni topishni
qiyinlashtiradi.

Yechim: har bir bosqich (build boshlandi/tugadi, start boshlandi, va h.k.) uchun
log fayliga bitta qatorli, aniq formatli "marker" yoziladi:

    ::STAGE:: 2026-07-20T12:00:00Z build_start   cmd="pip install -r requirements.txt"
    ::STAGE:: 2026-07-20T12:00:03Z build_ok       exit_code=0
    ::STAGE:: 2026-07-20T12:00:03Z run_start      cmd="python bot.py"

Bu formatni "AI-friendly" qiladigan narsa: har bir marker bitta qatorda, doim
bir xil tuzilishda (``::STAGE:: <ISO vaqt> <nomi> key=value ...``), va foydalanuvchi
kodining chiqishidan aniq ajratib turadi (``::STAGE::`` prefiksi bilan). Log faylini
to'g'ridan-to'g'ri Claude'ga (yoki boshqa AI'ga) tashlaganda, u qaysi qator qaysi
bosqichga tegishli ekanini formatga qarab tez ajrata oladi — matnni qayta o'qib
"bu qaysi bosqich edi" deb taxmin qilishiga hojat qolmaydi.
"""
import datetime


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_stage(log_file, stage: str, **fields) -> None:
    """log_file — ochiq fayl obyekti (text mode). Har chaqiruvda bitta qator yozadi va
    darhol diskka flush qiladi (jarayon keyin crash bo'lsa ham marker yo'qolmasin)."""
    parts = [f"::STAGE:: {_now()} {stage}"]
    for key, value in fields.items():
        value_str = str(value).replace("\n", " ").replace('"', "'")
        parts.append(f'{key}="{value_str}"')
    log_file.write(" ".join(parts) + "\n")
    log_file.flush()
