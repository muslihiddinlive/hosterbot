"""
services/resource_monitor.py

Render Free Tier'da butun platforma uchun bor-yo'g'i 512 MB FIZIK RAM bor, va bu
platformaning o'zi (asosiy manager bot) + BARCHA hosted user-botlar orasida
bo'linadi. deploy_manager'dagi RLIMIT_AS faqat VIRTUAL manzil maydonini cheklaydi
(ba'zi kutubxonalar import paytida katta .so fayllarni xotiraga map qilgani uchun
kerak) — HAQIQIY jismoniy xotirani emas. Shu sabab bir nechta bot bir vaqtda ko'p
RAM yesa, Render OOM-kill qilib, aybsiz boshqa botlarni ham "crashed" holatiga
olib kelishi mumkin edi.

Bu modul hozir ishlab turgan barcha hosted botlarning HAQIQIY (RSS) xotira
sarfini yig'ib, yangi bot ishga tushirish/qayta ishga tushirishdan OLDIN umumiy
byudjetga (TOTAL_RAM_BUDGET_MB) sig'ish-sig'maasligini tekshiradi.
"""
import psutil

from config import TOTAL_RAM_BUDGET_MB
from services.deploy_manager import list_running_pids


def _process_rss_mb(pid: int) -> float:
    """Berilgan pid va uning barcha bola processlarining (masalan shell -> python,
    chunki start_bot_process shell=True bilan ishga tushiradi) RSS xotirasini
    yig'ib, MB'da qaytaradi. Process allaqachon o'lgan bo'lsa 0 qaytaradi."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return 0.0

    total = 0
    try:
        total += proc.memory_info().rss
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    try:
        for child in proc.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return total / (1024 * 1024)


def total_bots_ram_mb() -> float:
    """Hozir ishlab turgan BARCHA hosted botlarning umumiy RSS xotirasi (MB)."""
    return sum(_process_rss_mb(pid) for pid in list_running_pids().values())


def platform_ram_mb() -> float:
    """Platformaning o'z (asosiy manager bot) processi ishlatayotgan RAM (MB)."""
    try:
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def can_start_new_bot(safety_margin_mb: float = 60.0) -> tuple[bool, float, float]:
    """
    Yangi bot ishga tushirish/qayta ishga tushirishdan OLDIN chaqiriladi.
    Hozirgi umumiy sarf + xavfsizlik zahirasi (yangi bot import/ishga tushish
    paytida sakrab ketishi mumkin bo'lgan RAM uchun) TOTAL_RAM_BUDGET_MB'dan
    oshib ketmasligini tekshiradi.

    Qaytaradi: (ruxsat_bormi, hozirgi_umumiy_mb, byudjet_mb)
    """
    used = platform_ram_mb() + total_bots_ram_mb()
    allowed = (used + safety_margin_mb) <= TOTAL_RAM_BUDGET_MB
    return allowed, used, TOTAL_RAM_BUDGET_MB
